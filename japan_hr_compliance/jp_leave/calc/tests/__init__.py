"""年休計算エンジンの単体テスト（frappe を import しない）。

リポジトリのルートで ``python3 -m unittest discover -s japan_hr_compliance/jp_leave/calc``、
または calc ディレクトリで ``python3 -m unittest discover`` で実行できる。
後者のためにリポジトリのルートを import パスに加える。
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[4])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
