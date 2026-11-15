"""帳票（PDF）用の日本語フォント。

フォント本体は依存先の japan_compliance が同梱している LifegenceJP（Noto Sans CJK JP の
Shift_JIS 範囲のサブセット、SIL Open Font License 1.1）を使う。OS に日本語フォントがない環境でも
PDF の日本語が表示される。@font-face の定義は japan_compliance/public/css/jp_print_fonts.css を
唯一の正とし、ここではそれを読み込んで <style> に差し込む（<link> は wkhtmltopdf のヘッダー・
フッターに引き継がれないため使わない）。
"""
import re
from functools import lru_cache

import frappe

FONT_STACK = '"LifegenceJP", "Noto Sans CJK JP", "IPAexGothic", sans-serif'


@lru_cache(maxsize=1)
def _load_font_css() -> str:
	path = frappe.get_app_path("japan_compliance", "public", "css", "jp_print_fonts.css")
	with open(path, encoding="utf-8") as f:
		css = f.read()
	return re.sub(r"/\*.*?\*/", "", css, flags=re.S).strip()


def jp_print_font_css() -> str:
	"""japan_compliance の @font-face 定義（コメントを除いたもの）を返す。jinja からも呼べる。

	jinja の methods フックは普通の関数しか登録しないため、キャッシュは内側の関数に付ける。
	"""
	return _load_font_css()


def style_block(extra_css: str = "") -> str:
	"""<style> 要素：@font-face と、body の font-family、追加の CSS。"""
	return f"<style>{jp_print_font_css()}\nbody{{font-family:{FONT_STACK}}}\n{extra_css}</style>"
