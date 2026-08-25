from ipyrf.controllers import BasePacingController, StaticPacingController


def test_base_next_send_returns_requested_bytes():
    controller = BasePacingController()
    assert controller.next_send(1200) == 1200


def test_static_next_send_preserves_size_without_pacing():
    controller = StaticPacingController(None, duration_seconds=1.0, interval_seconds=1.0)
    assert not controller.is_pacing()
    assert controller.next_send(1200) == 1200


def test_static_next_send_preserves_size_with_pacing():
    controller = StaticPacingController(
        50e6, duration_seconds=1.0, interval_seconds=1.0
    )
    assert controller.is_pacing()
    assert controller.next_send(1200) == 1200


def test_custom_controller_can_choose_size_and_skip():
    class CustomController(BasePacingController):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def next_send(self, n_bytes: int) -> int:
            self.calls += 1
            if self.calls == 1:
                return 0
            return min(100, n_bytes)

    controller = CustomController()
    assert controller.next_send(1200) == 0
    assert controller.next_send(1200) == 100
