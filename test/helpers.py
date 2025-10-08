import time


def wait_for_condition(condition_func, timeout=2, interval=0.1):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if condition_func():
            return True
        time.sleep(interval)
    return False
