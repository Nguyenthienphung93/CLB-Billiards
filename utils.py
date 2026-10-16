import re, unicodedata
from datetime import datetime

V_CODE_RE = re.compile(r'^V\d{2}-\d{6}$')
BELT_ORDER = [
    'Cấp 10','Cấp 9','Cấp 8','Cấp 7','Cấp 6','Cấp 5','Cấp 4','Cấp 3','Cấp 2','Cấp 1',
    '1 Đẳng','2 Đẳng','3 Đẳng','4 Đẳng','5 Đẳng','6 Đẳng','7 Đẳng','8 Đẳng','9 Đẳng','10 Đẳng'
]

def remove_accents(s: str) -> str:
    s = (s or '').replace('đ','d').replace('Đ','D')
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def normalize_key(s: str) -> str:
    return ''.join(remove_accents(str(s)).strip().lower().split())

def is_active(v) -> bool:
    return str(v).strip().lower() in ['có','co','1','yes','true']

def auto_generate_license(name: str, birthdate: str, existing: set[str]) -> str:
    parts = remove_accents(name.lower()).split()
    if not parts or birthdate.count('/') != 2:
        return ''
    day, month, year = birthdate.split('/')
    ten = parts[-1]
    lot = ''.join(w[0] for w in parts[:-1])
    base = f'HV_{ten}{lot}_{day}{month}{year[-2:]}'
    code = base
    i = 1
    while code in existing:
        code = f'HV_{ten}({i}){lot}_{day}{month}{year[-2:]}'
        i += 1
    return code

def parse_money_to_int(value: str) -> int:
    return int(str(value or '0').replace('đ','').replace('Đ','').replace('.','').replace(',','').strip() or 0)

def format_money(v: int) -> str:
    return f'{int(v):,} đ'.replace(',', '.')

def calc_tuition(month_count: int, half_month: bool, family: bool, exam: bool) -> int:
    hoc_phi = 500_000
    thi_cap_phi = 300_000
    total = 0
    if month_count == 1 and half_month:
        total = hoc_phi / 2
        if family: total *= 0.9
    elif month_count == 3:
        total = hoc_phi * 3 * (0.8 if family else 0.9)
    elif month_count in [4,5]:
        total = hoc_phi * 3 * (0.8 if family else 0.9) + hoc_phi * (month_count - 3)
    elif month_count in [1,2,6]:
        total = hoc_phi * month_count
        if family: total *= 0.9
    if exam:
        total += thi_cap_phi
    return int(total)

def build_month_codes(months: list[int], years: list[int]) -> tuple[str, str]:
    labels = ', '.join(str(m) for m in months)
    codes = ' - '.join(f'{m:02d}{y}' for m, y in zip(months, years))
    return (f'Tháng {labels}' if labels else '', codes)
