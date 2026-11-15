"""入力値の正規化と形式検証（従業員マスタ要件 B-1 v1.0 EMP-01・05・10・34）。

Frappe に依存しない純粋な関数だけを置く。エラーは ValueError で返し、呼び出し側で
frappe.throw に変換する。
"""

import re
import unicodedata

_ZEN_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_SEPARATORS = re.compile(r"[\s\-‐－−ー―]")

# 全角カナ（氏名カナ）で許す文字：カタカナ、長音、中黒、空白
_KANA_ALLOWED = re.compile(r"^[ァ-ヶー・　 ]*$")


def to_half_digits(value):
	return (value or "").translate(_ZEN_DIGITS)


def digits_only(value, label, length=None):
	"""区切り（ハイフン・空白）を除いた数字の文字列を返す。先頭のゼロは保持する。

	length を指定すると桁数を検証する。数字以外が含まれる場合は ValueError。
	"""
	if not value:
		return value
	s = _SEPARATORS.sub("", to_half_digits(str(value)))
	if not s.isdigit() or not s.isascii():
		raise ValueError(f"{label}は数字で入力してください（{value}）")
	if length and len(s) != length:
		raise ValueError(f"{label}は{length}桁です（{value}）")
	return s


def hiragana_to_katakana(value):
	return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in value)


def to_zenkaku_kana(value, label="カナ"):
	"""氏名カナを全角カタカナに正規化する（EMP-01）。半角カナ・ひらがなは変換する。

	NFKC で半角カナを全角にし（濁点も結合）、ひらがなをカタカナに変える。空白は全角1つに揃える。
	"""
	if not value:
		return value
	s = unicodedata.normalize("NFKC", value)
	s = hiragana_to_katakana(s)
	s = re.sub(r"[ 　]+", "　", s.strip())
	if not _KANA_ALLOWED.match(s):
		raise ValueError(f"{label}は全角カタカナで入力してください（{value}）")
	return s


# --- 全銀フォーマットの半角カナ（EMP-34 名義カナ） ------------------------------------------
# 半角カナ（U+FF61〜FF9F）を NFKC で全角にした対応表を逆引きして作る
_HALF = {}
for _cp in range(0xFF66, 0xFF9E):
	_HALF.setdefault(unicodedata.normalize("NFKC", chr(_cp)), chr(_cp))
_HALF["゙"] = "ﾞ"  # 結合濁点
_HALF["゚"] = "ﾟ"  # 結合半濁点
_HALF["ー"] = "-"  # 全銀では長音は使えないためハイフンにする
# 全銀では小書きのカナは使えないため大きいカナにする
_SMALL_TO_LARGE = str.maketrans("ｧｨｩｪｫｬｭｮｯ", "ｱｲｳｴｵﾔﾕﾖﾂ")
_ZENGIN_ALLOWED = re.compile(r"^[0-9A-Zｱ-ﾝﾞﾟ ()\-./｢｣,\\]*$")


def to_zengin_kana(value, label="口座名義カナ"):
	"""口座名義を全銀フォーマットで使える半角文字に正規化する（EMP-34）。

	全角・半角のカナ、ひらがな、英数字を受け付け、半角カナ・半角英大文字・数字と
	全銀の記号（空白 ( ) - . / ｢ ｣ , ￥）にする。小書きのカナは大きいカナに、長音はハイフンにする。
	使えない文字（漢字等）が残れば ValueError。
	"""
	if not value:
		return value
	s = hiragana_to_katakana(unicodedata.normalize("NFKC", value))
	s = unicodedata.normalize("NFD", s)  # 濁点を分ける（ガ → カ＋゛）
	s = "".join(_HALF.get(c, c) for c in s)
	s = s.upper().translate(_SMALL_TO_LARGE)
	s = s.replace("「", "｢").replace("」", "｣").replace("　", " ")
	s = re.sub(r" +", " ", s.strip())
	if not _ZENGIN_ALLOWED.match(s):
		raise ValueError(f"{label}に全銀フォーマットで使えない文字があります（{value}）")
	return s


# --- 全国地方公共団体コード（EMP-05） ----------------------------------------------------------
def lg_code_check_digit(first5):
	"""全国地方公共団体コードの検査数字。上位5桁に 6,5,4,3,2 を乗じた和を 11 で割った余りを 11 から引いた数の下1桁。"""
	total = sum(int(d) * w for d, w in zip(first5, (6, 5, 4, 3, 2)))
	return (11 - total % 11) % 10


def validate_lg_code(value):
	"""6桁の全国地方公共団体コード（検査数字付き）を検証して返す。"""
	code = digits_only(value, "市区町村コード", 6)
	if code and int(code[5]) != lg_code_check_digit(code[:5]):
		raise ValueError(f"市区町村コードの検査数字が一致しません（{value}）")
	return code


def format_postal_code(value):
	"""郵便番号を 123-4567 の形にする。"""
	code = digits_only(value, "郵便番号", 7)
	return f"{code[:3]}-{code[3:]}" if code else code


PREFECTURES = (
	"北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
	"埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
	"岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
	"鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
	"佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
