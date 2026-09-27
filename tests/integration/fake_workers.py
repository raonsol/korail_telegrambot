"""SubprocessLauncher 테스트용 워커 진입점 (forkserver가 이름으로 불러오므로 모듈 수준 함수)

모두 target(spec, log_path) 형태. 코레일 API는 호출하지 않는다.
"""

import json
import os
import sys
import time


def exit_with_code(spec, log_path):
    """결과 보고 없이 spec["code"]로 종료"""
    sys.exit(spec.get("code", 3))


def sleep_forever(spec, log_path):
    time.sleep(60)


def record_process_info(spec, log_path):
    """전달받은 명세, 명령행, 미리 불러온 모듈 여부를 로그 파일에 기록"""
    preloaded = "core.runner" in sys.modules  # 이 모듈은 core.runner를 import하지 않음
    with open(f"/proc/{os.getpid()}/cmdline", "rb") as f:
        cmdline = f.read().replace(b"\0", b" ").decode()
    with open(log_path, "w") as f:
        json.dump({"spec": spec, "cmdline": cmdline, "preloaded": preloaded}, f)


def run_with_no_trains(spec, log_path):
    """매번 "열차 없음"을 돌려주는 코레일로 실제 워커 진입점(run_process) 실행"""
    import telegramBot.korail_client as kc
    import core.runner as runner

    class NoTrains:
        def login(self, *a):
            return True

        def reserve_single_attempt(self, **kw):
            return {"success": False, "result": None, "error": "No trains available"}

        def close(self):
            pass

    kc.ReserveHandler = NoTrains
    original = runner.run_reservation

    def fast(spec, reporter, **kwargs):
        return original(
            spec,
            reporter,
            handler_factory=NoTrains,
            interval=0.01,
            progress_every=5,
            max_attempts=100000,
        )

    from telegramBot import worker

    worker.run_reservation = fast
    worker.run_process(spec, log_path)


def succeed_without_report(spec, log_path):
    """표는 잡았지만 성공 보고가 웹 서버에 전달되지 않은 워커 (실제 진입점의 결과 파일 경로 사용)"""
    import os

    from core.runner import result_file
    from telegramBot import worker

    def reserved_but_unreported(spec, reporter, on_success, **kwargs):
        on_success(train_info="KTX 101 서울→부산", attempts=4)
        return {"status": "success", "attempts": 4, "reported": False}

    worker.run_reservation = reserved_but_unreported
    results_dir = os.path.dirname(os.path.abspath(log_path))
    sys.exit(worker._run(spec, result_file(results_dir, spec["reservation_id"])))
