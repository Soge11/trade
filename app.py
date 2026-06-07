import time
from signal_engine import get_signal
from telegram_bot import send_alert

last_signal = None

while True:

    signal = get_signal()

    if signal != last_signal and signal != "HOLD":

        send_alert(
            f"BTCUSDT 15M Signal: {signal}"
        )

        last_signal = signal

    print(signal)

    time.sleep(900)