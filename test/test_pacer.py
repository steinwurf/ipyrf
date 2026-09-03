from ipyrf.pacer import Pacer


def test_pacer_first_send_is_immediate():
    pacer = Pacer(1e6)
    assert pacer.take(1250) == 0.0


def test_pacer_spaces_subsequent_sends():
    pacer = Pacer(8e3)  # 1000 bytes/s
    assert pacer.take(100) == 0.0
    wait = pacer.take(100)
    assert 0.09 < wait < 0.11


def test_pacer_late_send_does_not_double_charge(monkeypatch):
    times = iter([0.0, 1.0])
    monkeypatch.setattr("ipyrf.pacer.time.monotonic", lambda: next(times))
    pacer = Pacer(8e3)  # 100 bytes cost 0.1s
    assert pacer.take(100) == 0.0
    # Already 1s late: send immediately rather than waiting another slot.
    assert pacer.take(100) == 0.0
