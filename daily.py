"""Run the whole home desk in one go: holdings dashboard, movers, options tape.

Asks for the day's session token once (the first sub-run caches it), then runs
all three. This is the single command to type each morning.

    python daily.py
"""

import run
import morning
import fno_tape
import fno_positions
import dashboard_html


def main():
    print("\n=== 1/5 Holdings dashboard ===")
    run.main()
    print("\n=== 2/5 Movers ===")
    morning.main()
    print("\n=== 3/5 Options tape ===")
    fno_tape.main()
    print("\n=== 4/5 F&O positions ===")
    fno_positions.main()
    print("\n=== 5/5 Browser dashboard (both accounts) ===")
    dashboard_html.main()
    print("\nAll done. Files are in your vault dashboards folder.")


if __name__ == "__main__":
    main()
