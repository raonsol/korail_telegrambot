"""
Celery tasks for background reservation processing
"""

import time
import logging
from datetime import datetime
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
import requests
import redis

from .korail_client import ReserveHandler
from config import celery_settings, web_settings
from korail2 import ReserveOption, TrainType

logger = logging.getLogger(__name__)

# Initialize Celery app
app = Celery("korail_reservations")
app.conf.update(
    broker_url=celery_settings.celery_broker,
    result_backend=celery_settings.celery_result_backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=celery_settings.reservation_timeout,
    task_soft_time_limit=celery_settings.reservation_timeout - 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=50,
)


@app.task(bind=True, max_retries=3)
def reservation_task(self, chat_id: int, reservation_data: dict, callback_url: str):
    """
    Background task for KTX reservation

    Args:
        chat_id: Telegram chat ID
        reservation_data: Dictionary containing reservation details
        callback_url: URL to send status updates
    """
    # Initialize Redis client to check reservation status
    redis_client = None

    # Use Celery task ID as unique key to prevent duplicate task execution
    # This allows the same user to make multiple reservations simultaneously
    # (e.g., different trains, or retry after failure)
    reservation_key = f"reservation_task:{self.request.id}"

    try:
        redis_client = redis.Redis.from_url(
            web_settings.redis_url,
            db=web_settings.redis_db,
            decode_responses=True,
        )

        # Check if THIS SPECIFIC reservation is already completed
        reservation_status = redis_client.hget(reservation_key, "status")
        if reservation_status == "completed":
            logger.info(
                f"Reservation already completed for key: {reservation_key}, skipping task"
            )
            return {
                "status": "already_completed",
                "message": "Reservation already completed",
            }

        # Mark THIS SPECIFIC task as started
        redis_client.hset(reservation_key, "status", "running")

    except Exception as redis_error:
        logger.warning(
            f"Redis connection failed, continuing without state check: {redis_error}"
        )
        redis_client = None

    try:
        logger.info(f"Starting reservation task for chat_id: {chat_id}")

        # Send start notification with task_id
        _send_callback(
            callback_url,
            chat_id,
            "started",
            "Reservation process started",
            self.request.id,
        )

        # Initialize reservation handler
        reserve_handler = ReserveHandler()

        # Login to Korail
        if not reserve_handler.login(
            reservation_data["korail_id"], reservation_data["korail_pw"]
        ):
            error_msg = "Korail login failed"
            logger.error(f"Login failed for chat_id: {chat_id}")
            _send_callback(callback_url, chat_id, "failed", error_msg, self.request.id)
            return {"status": "failed", "message": error_msg}

        # Perform reservation attempts with Celery-specific retry logic
        max_attempts = 1000  # Maximum number of attempts
        attempt_count = 0

        while attempt_count < max_attempts:
            try:
                # Check if THIS SPECIFIC reservation is already completed before each attempt
                if redis_client:
                    try:
                        reservation_status = redis_client.hget(
                            reservation_key, "status"
                        )
                        if reservation_status == "completed":
                            logger.info(
                                f"Reservation already completed for key: {reservation_key}, stopping task"
                            )
                            return {
                                "status": "already_completed",
                                "message": "Reservation already completed by another task",
                            }
                    except Exception as e:
                        logger.warning(f"Redis status check failed: {e}")

                attempt_count += 1
                logger.info(
                    f"Celery reservation attempt {attempt_count} for chat_id: {chat_id}"
                )

                # Convert train type and seat preference
                train_type_map = {"KTX": TrainType.KTX, "ALL": TrainType.ALL}
                seat_type_map = {
                    "general": ReserveOption.GENERAL_FIRST,
                    "general_only": ReserveOption.GENERAL_ONLY,
                    "special": ReserveOption.SPECIAL_FIRST,
                    "special_only": ReserveOption.SPECIAL_ONLY,
                }

                train_type = train_type_map.get(
                    reservation_data["train_type"], TrainType.KTX
                )
                seat_type = seat_type_map.get(
                    reservation_data["prefer_seat_type"], ReserveOption.GENERAL_FIRST
                )

                # Single attempt reservation (Celery mode)
                result = reserve_handler.reserve_single_attempt(
                    depDate=reservation_data["dep_date"],
                    srcLocate=reservation_data["dep_station"],
                    dstLocate=reservation_data["arr_station"],
                    depTime=reservation_data["dep_time"],
                    trainType=train_type,
                    special=seat_type,
                    maxDepTime=reservation_data.get("arr_time", "2400"),
                )

                # Check if reservation was successful
                if result["success"]:
                    if result["result"] == "duplicate_reservation":
                        success_msg = "Reservation already completed successfully!"
                    else:
                        success_msg = str(result["result"])

                    logger.info(
                        f"Reservation successful for chat_id: {chat_id} after {attempt_count} attempts"
                    )

                    # Mark THIS SPECIFIC reservation as completed in Redis to prevent retries
                    if redis_client:
                        try:
                            redis_client.hset(reservation_key, "status", "completed")
                        except Exception as e:
                            logger.warning(f"Failed to update Redis status: {e}")

                    _send_callback(
                        callback_url, chat_id, "success", success_msg, self.request.id
                    )
                    return {
                        "status": "success",
                        "message": success_msg,
                        "attempts": attempt_count,
                    }

                # No success, wait before next attempt
                time.sleep(2)

                # Send periodic status updates
                if attempt_count % 50 == 0:
                    status_msg = (
                        f"Attempt {attempt_count}/{max_attempts} - Still searching..."
                    )
                    _send_callback(
                        callback_url, chat_id, "progress", status_msg, self.request.id
                    )

            except Exception as e:
                error_str = str(e)
                if attempt_count % 10 == 0:
                    logger.warning(
                        f"Reservation attempt {attempt_count} failed: {error_str}"
                    )

                # Re-login if session expired
                if "login" in error_str.lower() or "session" in error_str.lower():
                    logger.info(f"Re-logging in for chat_id: {chat_id}")
                    if not reserve_handler.login(
                        reservation_data["korail_id"], reservation_data["korail_pw"]
                    ):
                        error_msg = "Re-login failed"
                        _send_callback(
                            callback_url, chat_id, "failed", error_msg, self.request.id
                        )
                        return {"status": "failed", "message": error_msg}

                time.sleep(1)
                continue

        # If we reach here, max attempts exceeded
        timeout_msg = f"Reservation timeout after {max_attempts} attempts"
        logger.warning(f"Reservation timeout for chat_id: {chat_id}")
        _send_callback(callback_url, chat_id, "failed", timeout_msg, self.request.id)
        return {"status": "timeout", "message": timeout_msg, "attempts": attempt_count}

    except SoftTimeLimitExceeded:
        # Check if THIS SPECIFIC reservation was already completed before failing
        if redis_client:
            try:
                status = redis_client.hget(reservation_key, "status")
                if status == "completed":
                    logger.info(
                        f"Task exceeded time limit but reservation was already completed for key: {reservation_key}"
                    )
                    return {
                        "status": "success",
                        "message": "Reservation completed before timeout",
                    }
            except Exception:
                pass

        error_msg = "Reservation task soft time limit exceeded"
        logger.warning(f"예약 시도 중 오류 발생: {error_msg}")
        _send_callback(
            callback_url,
            chat_id,
            "failed",
            "최대 시도 횟수를 초과하여 예약이 중단되었습니다.",
            self.request.id,
        )
        return {"status": "failed", "message": error_msg}

    except Exception as e:
        # Check if reservation was already completed before failing
        if redis_client:
            try:
                status = redis_client.hget(f"reservation:{chat_id}", "status")
                if status == "completed":
                    logger.info(
                        f"Task failed but reservation was already completed for chat_id: {chat_id}"
                    )
                    return {
                        "status": "success",
                        "message": "Reservation completed before error",
                    }
            except Exception:
                pass

        error_msg = f"Reservation task failed: {str(e)}"
        logger.error(f"Reservation task error for chat_id: {chat_id}: {str(e)}")
        _send_callback(callback_url, chat_id, "failed", error_msg, self.request.id)
        return {"status": "failed", "message": error_msg}


def _send_callback(
    callback_url: str, chat_id: int, status: str, message: str, task_id: str = None
):
    """Send status update to callback URL"""
    try:
        payload = {
            "user_id": chat_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }

        # Include task_id if provided
        if task_id:
            payload["task_id"] = task_id

        # Add status-specific fields
        if status == "success":
            payload["train_info"] = message
        elif status == "failed":
            payload["error"] = message
        else:
            payload["message"] = message

        # Callback URL is already properly configured for the deployment mode

        response = requests.post(callback_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Successfully sent callback: status={status}, chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Failed to send callback: {str(e)}")


if __name__ == "__main__":
    app.start()
