import sys
import signal
import logging
from datetime import datetime
from .korail_client import ReserveHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f'worker_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

sys.setrecursionlimit(10**7)


# def reserve(self, depDate, srcLocate, dstLocate, depTime='000000', trainType=TrainType.KTX, special=ReserveOption.GENERAL_FIRST, chatId="", maxDepTime'2400'):
class BackProcess(object):

    def __init__(self):
        try:
            self.username = sys.argv[1]
            self.password = sys.argv[2]
            self.depDate = sys.argv[3]
            self.srcLocate = sys.argv[4]
            self.dstLocate = sys.argv[5]
            self.depTime = sys.argv[6]
            self.trainType = sys.argv[7]
            self.specialInfo = sys.argv[8]
            self.chatId = sys.argv[9]
            self.maxDepTime = sys.argv[10]

            self.reserve_handler = ReserveHandler()
            self.max_retries = 3
            self.retry_count = 0

            # Register signal handlers
            signal.signal(signal.SIGTERM, self.handle_termination)
            signal.signal(signal.SIGINT, self.handle_termination)

            if not self.reserve_handler.login(self.username, self.password):
                raise Exception("Failed to login")

        except Exception as e:
            logger.error(f"Initialization error: {str(e)}")
            self.send_error_message(f"초기화 중 오류 발생: {str(e)}")
            sys.exit(1)

    def handle_termination(self, signum, frame):
        logger.info(f"Received termination signal {signum}")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        try:
            if hasattr(self, "reserve_handler"):
                self.reserve_handler.sendBotStateChange(
                    self.chatId, "프로세스가 종료되었습니다.", 0
                )
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")

    def send_error_message(self, message):
        try:
            self.reserve_handler.sendBotStateChange(self.chatId, message, 0)
        except Exception as e:
            logger.error(f"Failed to send error message: {str(e)}")

    def run(self):
        try:
            while self.retry_count < self.max_retries:
                try:
                    logger.info(f"Starting reservation attempt {self.retry_count + 1}")
                    self.reserve_handler.reserve(
                        self.depDate,
                        self.srcLocate,
                        self.dstLocate,
                        self.depTime,
                        self.trainType,
                        self.specialInfo,
                        self.chatId,
                        self.maxDepTime,
                    )
                    break
                except Exception as e:
                    self.retry_count += 1
                    logger.error(
                        f"Reservation attempt {self.retry_count} failed: {str(e)}"
                    )
                    if self.retry_count < self.max_retries:
                        logger.info("Retrying...")
                        continue
                    else:
                        raise

        except Exception as e:
            logger.error(
                f"Reservation failed after {self.max_retries} attempts: {str(e)}"
            )
            self.send_error_message(f"예약 중 오류 발생: {str(e)}")
        finally:
            self.cleanup()
            logger.info(f"Reserve Job for {self.username} is end")


if __name__ == "__main__":
    proc1 = BackProcess()
    proc1.run()
