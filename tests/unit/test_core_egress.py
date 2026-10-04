"""코레일 출구: 설정 해석, 계정 고정 배정, 출구별 요청 간격과 차단 대기"""

import fakeredis
import pytest

from core.egress import (
    BLOCK_BACKOFF_SECONDS,
    BLOCK_LEVEL_RESET_SECONDS,
    DIRECT,
    Egress,
    EgressPool,
    FileGate,
    MemoryGate,
    RedisGate,
    assign_egress,
    file_gate_path,
    parse_egresses,
)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestParseEgresses:
    def test_empty_means_single_direct_egress(self):
        assert parse_egresses("") == [Egress(DIRECT)]
        assert parse_egresses('  ""  ') == [Egress(DIRECT)]

    def test_ids_and_proxies(self):
        egresses = parse_egresses(
            "direct, home1=socks5h://user:pw@100.64.0.2:1080,home2=http://10.0.0.3:3128"
        )
        assert egresses == [
            Egress("direct"),
            Egress("home1", "socks5h://user:pw@100.64.0.2:1080"),
            Egress("home2", "http://10.0.0.3:3128"),
        ]
        assert egresses[0].is_direct and not egresses[1].is_direct

    @pytest.mark.parametrize(
        "value",
        [
            "bad id=socks5h://h:1",
            "a=ftp://h:1",
            "a=socks5h://",
            "a,a=socks5h://h:1",
        ],
    )
    def test_invalid_config(self, value):
        with pytest.raises(ValueError):
            parse_egresses(value)

    def test_error_does_not_include_proxy_credentials(self):
        with pytest.raises(ValueError) as e:
            parse_egresses("a=ftp://user:secret@h:1")
        assert "secret" not in str(e.value)


class TestAssignEgress:
    EGRESSES = [Egress(f"e{i}", f"socks5h://h{i}:1080") for i in range(4)]

    def test_same_account_always_gets_same_egress(self):
        first = assign_egress("010-1234-5678", self.EGRESSES)
        assert assign_egress("010-1234-5678", self.EGRESSES) == first
        # 하이픈 유무와 관계없이 같은 계정
        assert assign_egress("01012345678", self.EGRESSES) == first
        # 목록 순서가 바뀌어도 같은 출구
        assert assign_egress("010-1234-5678", self.EGRESSES[::-1]) == first

    def test_accounts_are_spread_over_egresses(self):
        used = {assign_egress(f"010{n:08d}", self.EGRESSES).id for n in range(200)}
        assert used == {e.id for e in self.EGRESSES}

    def test_adding_an_egress_only_moves_accounts_to_it(self):
        accounts = [f"010{n:08d}" for n in range(300)]
        more = self.EGRESSES + [Egress("e4", "socks5h://h4:1080")]
        moved = [
            a
            for a in accounts
            if assign_egress(a, self.EGRESSES) != assign_egress(a, more)
        ]
        assert moved  # 새 출구도 계정을 받음
        assert all(assign_egress(a, more).id == "e4" for a in moved)


class TestMemoryGate:
    def test_requests_are_spaced_per_egress(self):
        clock = Clock()
        gate = MemoryGate(rpm=60, clock=clock)
        # 같은 시각에 세 예약이 순서를 잡으면 1초 간격으로 줄을 섬
        assert [gate.reserve_slot() for _ in range(3)] == [0.0, 1.0, 2.0]
        clock.now += 10
        assert gate.reserve_slot() == 0.0

    def test_no_limit(self):
        gate = MemoryGate(rpm=0)
        assert gate.reserve_slot() == gate.reserve_slot() == 0.0

    def test_block_backoff_grows_and_is_reported_once(self):
        clock = Clock()
        gate = MemoryGate(clock=clock)
        assert gate.blocked_for() == 0
        assert gate.report_block() == BLOCK_BACKOFF_SECONDS[0]
        # 같은 차단을 다른 예약이 보고해도 대기 시간이 늘지 않음
        clock.now += 60
        assert gate.report_block() == BLOCK_BACKOFF_SECONDS[0] - 60
        assert gate.blocked_for() == BLOCK_BACKOFF_SECONDS[0] - 60

        clock.now += BLOCK_BACKOFF_SECONDS[0]
        assert gate.blocked_for() == 0
        assert gate.report_block() == BLOCK_BACKOFF_SECONDS[1]
        for expected in BLOCK_BACKOFF_SECONDS[2:] + (BLOCK_BACKOFF_SECONDS[-1],):
            clock.now += gate.blocked_for()
            assert gate.report_block() == expected

    def test_backoff_resets_after_quiet_period(self):
        clock = Clock()
        gate = MemoryGate(clock=clock)
        gate.report_block()
        clock.now += BLOCK_LEVEL_RESET_SECONDS + 1
        assert gate.report_block() == BLOCK_BACKOFF_SECONDS[0]


class TestFileGate:
    def test_state_is_shared_through_the_file(self, tmp_path):
        clock = Clock()
        path = file_gate_path(str(tmp_path), "home1")
        a = FileGate(path, rpm=30, clock=clock)
        b = FileGate(path, rpm=30, clock=clock)  # 다른 워커 프로세스

        assert a.reserve_slot() == 0.0
        assert b.reserve_slot() == 2.0
        assert b.report_block() == BLOCK_BACKOFF_SECONDS[0]
        assert a.blocked_for() == BLOCK_BACKOFF_SECONDS[0]
        assert a.report_block() == BLOCK_BACKOFF_SECONDS[0]  # 같은 차단

    def test_corrupted_state_is_reset(self, tmp_path):
        path = tmp_path / "egress_x.json"
        path.write_text("{not json")
        gate = FileGate(str(path), rpm=60, clock=Clock())
        assert gate.blocked_for() == 0
        assert gate.reserve_slot() == 0.0

    def test_unwritable_state_does_not_stop_reservations(self, tmp_path):
        gate = FileGate(str(tmp_path / "missing" / "egress.json"), rpm=60)
        assert gate.reserve_slot() == 0.0
        assert gate.blocked_for() == 0.0
        assert gate.report_block() == 0.0


class TestRedisGate:
    @pytest.fixture
    def client(self):
        return fakeredis.FakeRedis(decode_responses=True)

    def test_requests_are_spaced_across_workers(self, client):
        a = RedisGate(client, "home1", rpm=60)
        b = RedisGate(client, "home1", rpm=60)  # 다른 워커 서버
        other = RedisGate(client, "home2", rpm=60)

        waits = [a.reserve_slot(), b.reserve_slot(), a.reserve_slot()]
        assert waits[0] == pytest.approx(0, abs=0.05)
        assert waits[1] == pytest.approx(1, abs=0.05)
        assert waits[2] == pytest.approx(2, abs=0.05)
        # 출구마다 따로
        assert other.reserve_slot() == pytest.approx(0, abs=0.05)

    def test_block_is_shared_and_escalates_once_per_block(self, client):
        a = RedisGate(client, "home1")
        b = RedisGate(client, "home1")

        assert a.blocked_for() == 0
        assert a.report_block() == BLOCK_BACKOFF_SECONDS[0]
        assert b.blocked_for() == pytest.approx(BLOCK_BACKOFF_SECONDS[0], abs=1)
        assert b.report_block() == pytest.approx(BLOCK_BACKOFF_SECONDS[0], abs=1)
        assert client.get("egress_block_level:home1") == "1"
        # 연속 차단 단계는 차단 시작부터 2시간 (MemoryGate/FileGate와 같은 기준)
        assert client.ttl("egress_block_level:home1") == pytest.approx(
            BLOCK_LEVEL_RESET_SECONDS, abs=2
        )

        client.delete("egress_block:home1")  # 대기 시간이 지남
        assert b.report_block() == BLOCK_BACKOFF_SECONDS[1]
        assert a.blocked_for() == pytest.approx(BLOCK_BACKOFF_SECONDS[1], abs=1)
        assert RedisGate(client, "home2").blocked_for() == 0

    def test_redis_errors_do_not_stop_reservations(self):
        class Broken:
            def __getattr__(self, name):
                raise ConnectionError("redis down")

        gate = RedisGate(Broken(), "home1", rpm=60)
        assert gate.reserve_slot() == 0.0
        assert gate.blocked_for() == 0.0
        assert gate.report_block() == BLOCK_BACKOFF_SECONDS[0]


class TestEgressPool:
    def test_from_settings(self):
        class Settings:
            korail_egresses = "a=socks5h://h:1,b"
            korail_egress_rpm = 30
            korail_egress_max_active = 5

        pool = EgressPool.from_settings(Settings())
        assert pool.ids == ["a", "b"]
        assert pool.rpm == 30 and pool.max_active == 5
        assert pool.for_account("010-1111-2222").id in ("a", "b")
        # 출구마다 상태 하나
        assert pool.gate("a") is pool.gate("a")
        assert pool.gate("a") is not pool.gate("b")
        assert "socks5h" not in pool.describe()
