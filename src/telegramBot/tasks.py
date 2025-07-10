"""
Celery tasks for background reservation processing
"""

import os
import time
import logging
from datetime import datetime
from celery import Celery
from celery.exceptions import Retry
import requests

from .korail_client import ReserveHandler
from config import celery_settings

logger = logging.getLogger(__name__)

# Initialize Celery app
app = Celery('korail_reservations')
app.conf.update(
    broker_url=celery_settings.celery_broker,
    result_backend=celery_settings.celery_result_backend,
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Seoul',
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
    try:
        logger.info(f"Starting reservation task for chat_id: {chat_id}")
        
        # Send start notification
        _send_callback(callback_url, chat_id, 'started', 'Reservation process started')
        
        # Initialize reservation handler
        reserve_handler = ReserveHandler()
        
        # Login to Korail
        if not reserve_handler.login(
            reservation_data['korail_id'], 
            reservation_data['korail_pw']
        ):
            error_msg = "Korail login failed"
            logger.error(f"Login failed for chat_id: {chat_id}")
            _send_callback(callback_url, chat_id, 'failed', error_msg)
            return {'status': 'failed', 'message': error_msg}
        
        # Perform reservation attempts
        max_attempts = 1000  # Maximum number of attempts
        attempt_count = 0
        
        while attempt_count < max_attempts:
            try:
                attempt_count += 1
                logger.info(f"Reservation attempt {attempt_count} for chat_id: {chat_id}")
                
                # Attempt reservation
                result = reserve_handler.reserveTrain(
                    reservation_data['dep_date'],
                    reservation_data['dep_station'],
                    reservation_data['arr_station'],
                    reservation_data['dep_time'],
                    reservation_data['train_type'],
                    reservation_data['prefer_seat_type']
                )
                
                if result and result.get('success'):
                    success_msg = f"Reservation successful! {result.get('message', '')}"
                    logger.info(f"Reservation successful for chat_id: {chat_id}")
                    _send_callback(callback_url, chat_id, 'success', success_msg)
                    return {'status': 'success', 'message': success_msg, 'attempts': attempt_count}
                
                # Wait before next attempt
                time.sleep(2)
                
                # Send periodic status updates
                if attempt_count % 50 == 0:
                    status_msg = f"Attempt {attempt_count}/{max_attempts} - Still searching..."
                    _send_callback(callback_url, chat_id, 'progress', status_msg)
                
            except Exception as e:
                if attempt_count % 10 == 0:
                    logger.warning(f"Reservation attempt {attempt_count} failed: {str(e)}")
                
                # Re-login if session expired
                if 'login' in str(e).lower() or 'session' in str(e).lower():
                    logger.info(f"Re-logging in for chat_id: {chat_id}")
                    if not reserve_handler.login(
                        reservation_data['korail_id'], 
                        reservation_data['korail_pw']
                    ):
                        error_msg = "Re-login failed"
                        _send_callback(callback_url, chat_id, 'failed', error_msg)
                        return {'status': 'failed', 'message': error_msg}
                
                time.sleep(1)
                continue
        
        # If we reach here, max attempts exceeded
        timeout_msg = f"Reservation timeout after {max_attempts} attempts"
        logger.warning(f"Reservation timeout for chat_id: {chat_id}")
        _send_callback(callback_url, chat_id, 'timeout', timeout_msg)
        return {'status': 'timeout', 'message': timeout_msg, 'attempts': attempt_count}
        
    except Exception as e:
        error_msg = f"Reservation task failed: {str(e)}"
        logger.error(f"Reservation task error for chat_id: {chat_id}: {str(e)}")
        _send_callback(callback_url, chat_id, 'failed', error_msg)
        return {'status': 'failed', 'message': error_msg}


def _send_callback(callback_url: str, chat_id: int, status: str, message: str):
    """Send status update to callback URL"""
    try:
        response = requests.post(
            callback_url,
            json={
                'chat_id': chat_id,
                'status': status,
                'message': message,
                'timestamp': datetime.now().isoformat()
            },
            timeout=10
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send callback: {str(e)}")


if __name__ == '__main__':
    app.start()