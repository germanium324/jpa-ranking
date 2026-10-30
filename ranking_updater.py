#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import pdfplumber
import pandas as pd
from io import BytesIO
import datetime
import json
import os
import re
from urllib.parse import urljoin

# --- 設定値 ---
BASE_URL = "http://www.poolplayers.jp"
STANDINGS_URL = f"{BASE_URL}/standings/"
STANDINGS_PAGE_CANDIDATES = [
    STANDINGS_URL,
    STANDINGS_URL.replace("http://", "https://"),
    STANDINGS_URL.replace("://www.", "://"),
    STANDINGS_URL.replace("http://www.", "https://"),
    STANDINGS_URL.replace("http://www.", "http://"),
]
TARGET_DIVISION_NAME = "028 COLLEGE (TUE)"
DIVISION_CODE = TARGET_DIVISION_NAME.split()[0]  # '028'
JSON_FILENAME = 'ranking_data.json'

# チーム名マッピング辞書（名簿PDFが取得できない場合のフォールバック用）
TEAM_NAME_MAP = {
    '1': 'Anamae Family', '2': 'Watawata Nabenabe', '3': 'Wagamama Fantasy', '4': 'Kairiki',
    '5': 'Tamanchu Musou', '6': 'Tamanchu Kokushi', '7': 'Shou Time', '8': 'Go Go Chance!',
    '9': 'Aizawanwan', '10': 'Ridge Flow', '11': 'Tamatorino Okina'
}

# --- 1. 最新のPDF URLを特定 ---
def _find_target_row(soup):
    """028ディビジョンの行を特定する。まずディビジョン名の完全一致で探し、
    見つからない場合はディビジョンコードを含むPDFリンクを持つ行をスキャンする。"""
    # 1) 完全一致で検索
    target_cell = soup.find(string=TARGET_DIVISION_NAME)
    if target_cell:
        row = target_cell.find_parent('tr')
        if row:
            return row

    # 2) 部分一致（ディビジョンコードを含むセルテキスト）で検索
    for cell in soup.find_all(['td', 'th']):
        if DIVISION_CODE in (cell.get_text() or ''):
            row = cell.find_parent('tr')
            if row:
                # この行に028のPDFリンクがあるか確認
                for a in row.find_all('a'):
                    href = a.get('href', '')
                    if re.search(rf'{DIVISION_CODE}\d+\.pdf', href, re.IGNORECASE):
                        print("部分一致でディビジョン行を検出しました（フォールバック）")
                        return row

    # 3) 全<tr>をスキャンして028のPDFリンクを持つ行を探す
    for tr in soup.find_all('tr'):
        for a in tr.find_all('a'):
            href = a.get('href', '')
            if re.search(rf'[SRP]{DIVISION_CODE}\d+\.pdf', href, re.IGNORECASE):
                print("PDFリンクスキャンでディビジョン行を検出しました（フォールバック）")
                return tr

    return None


def _parse_pdf_date_token(token):
    """PDFファイル名の6桁日付(MMDDYY)をdateに変換する。失敗時はNone。"""
    if not token or len(token) != 6 or not token.isdigit():
        return None
    try:
        month = int(token[0:2])
        day = int(token[2:4])
        year = 2000 + int(token[4:6])
        return datetime.date(year, month, day)
    except ValueError:
        return None


def fetch_standings_page():
    """スタンディングページを取得し、解析済みHTMLと実際の取得URLを返す。"""
    last_error = None
    seen = set()
    for standings_url in STANDINGS_PAGE_CANDIDATES:
        if standings_url in seen:
            continue
        seen.add(standings_url)
        print(f"スタンディングページを解析中: {standings_url}")
        try:
            response = requests.get(standings_url, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser'), response.url
        except requests.exceptions.RequestException as e:
            last_error = e

    if last_error:
        print(f"スタンディングページの取得に失敗しました: {last_error}")
    return None, None


def find_pdf_urls(soup, standings_page_url):
    """スタンディングページからS/R/P型PDFの最新URLを抽出する。"""
    if soup is None or not standings_page_url:
        return {}

    target_row = _find_target_row(soup)
    link_groups = []
    if target_row:
        link_groups.append((0, target_row.find_all('a')))
    link_groups.append((1, soup.find_all('a')))

    candidates_by_type = {'S': [], 'R': [], 'P': []}
    seen_urls = {key: set() for key in candidates_by_type}

    for scope_priority, links in link_groups:
        for a in links:
            href = a.get('href')
            if not href:
                continue

            full_url = urljoin(standings_page_url, href)
            match = re.search(rf'([SRP]){DIVISION_CODE}(\d+)\.pdf', full_url, re.IGNORECASE)
            if not match:
                continue

            pdf_type = match.group(1).upper()
            token = match.group(2)
            if full_url in seen_urls[pdf_type]:
                continue

            seen_urls[pdf_type].add(full_url)
            candidates_by_type[pdf_type].append((
                _parse_pdf_date_token(token),
                token,
                scope_priority,
                full_url
            ))

    pdf_urls = {}
    for pdf_type, candidates in candidates_by_type.items():
        if not candidates:
            continue
        candidates.sort(
            key=lambda x: (
                x[0] is None,
                -(x[0].toordinal() if x[0] else datetime.date.min.toordinal()),
                -int(x[1]) if x[1].isdigit() else 0,
                x[2]
            )
        )
        pdf_urls[pdf_type] = candidates[0][3]

    return pdf_urls


def find_schedule_pdf_url(soup, standings_page_url):
    """スタンディングページから028 + MMDDYY形式のスケジュールPDFを探す。"""
    if soup is None or not standings_page_url:
        return None

    candidates = []
    seen_urls = set()
    target_row = _find_target_row(soup)
    link_groups = [target_row.find_all('a')] if target_row else []
    link_groups.append(soup.find_all('a'))
    for links in link_groups:
        for link in links:
            href = link.get('href')
            if not href:
                continue
            full_url = urljoin(standings_page_url, href)
            match = re.search(rf'(?:^|/){DIVISION_CODE}(\d{{6}})\.pdf(?:$|[?#])', full_url, re.IGNORECASE)
            if not match or full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            candidates.append((_parse_pdf_date_token(match.group(1)), full_url))

    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (
        candidate[0] is None,
        -(candidate[0].toordinal() if candidate[0] else datetime.date.min.toordinal()),
        candidate[1]
    ))
    return candidates[0][1]


def _schedule_date(value, default_year):
    """PDF内の日付をISO形式にする。年がなければPDF名の年を使う。"""
    normalized = re.sub(r'\s+', '', str(value or ''))
    iso_match = re.search(r'\b(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b', normalized)
    if iso_match:
        try:
            return datetime.date(
                int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
            ).isoformat()
        except ValueError:
            return None
    match = re.search(r'(?<!\d)(\d{1,2})[./月-](\d{1,2})(?:日)?(?:[./-](\d{2,4}))?', normalized)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    year_text = match.group(3)
    year = int(year_text) if year_text else default_year
    if year < 100:
        year += 2000
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _schedule_team_id(value):
    """チームコード(02801)、No.表記、またはチーム番号をIDへ変換する。"""
    normalized = re.sub(r'\s+', '', str(value or '')).translate(
        str.maketrans('０１２３４５６７８９', '0123456789')
    )
    match = re.fullmatch(r'0280?(\d{1,2})', normalized)
    if not match:
        match = re.fullmatch(r'(?:No\.?|チームNo\.?|チーム)?0*(\d{1,2})', normalized, re.IGNORECASE)
    if not match:
        return None
    return str(int(match.group(1)))


def _is_schedule_bye(value):
    return bool(re.search(r'\bbye\b|休み|休戦', str(value or ''), re.IGNORECASE))


def _is_red_schedule_cell(page, cell):
    """赤枠と重なるセルを自空として判定する。"""
    if not cell or len(cell) != 4:
        return False
    x0, top, x1, bottom = cell
    for shape in page.rects + page.curves:
        color = shape.get('stroking_color') or shape.get('non_stroking_color')
        if not isinstance(color, (tuple, list)) or len(color) < 3:
            continue
        red, green, blue = color[:3]
        if red < 0.65 or green > 0.4 or blue > 0.4:
            continue
        sx0, stop, sx1, sbottom = shape.get('x0', 0), shape.get('top', 0), shape.get('x1', 0), shape.get('bottom', 0)
        if sx0 <= (x0 + x1) / 2 <= sx1 and stop <= (top + bottom) / 2 <= sbottom:
            return True
        if sx0 <= x0 and sx1 >= x1 and stop <= top and sbottom >= bottom:
            return True
    return False


def extract_schedule(pdf_file, pdf_url=None):
    """日程PDFから対戦・Byeを抽出し、チームごとの試合データを返す。"""
    if pdf_file is None:
        return []

    filename_date = re.search(rf'{DIVISION_CODE}(\d{{6}})\.pdf', str(pdf_url or ''), re.IGNORECASE)
    file_date = _parse_pdf_date_token(filename_date.group(1)) if filename_date else None
    default_year = file_date.year if file_date else datetime.date.today().year
    matchups = {}

    def add_event(date_value, team_a, team_b=None, venue='山水'):
        if not date_value or not team_a:
            return
        if team_b and team_a == team_b:
            return
        opponent_ids = sorted([team_a, team_b]) if team_b else [team_a, 'bye']
        key = (date_value, *opponent_ids)
        matchups.setdefault(key, {
            'date': date_value,
            'team_ids': [team_a, team_b] if team_b else [team_a],
            'venue': venue if team_b else ''
        })

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            for table in tables:
                rows = table.rows
                cells = table.extract()
                if not rows or not cells:
                    continue

                # 日付が列見出しに並ぶ表では、各チーム行のセルが対戦相手を示す。
                header_index = max(
                    range(len(cells)),
                    key=lambda index: sum(_schedule_date(cell, default_year) is not None for cell in (cells[index] or []))
                )
                header_dates = {
                    column: _schedule_date(value, default_year)
                    for column, value in enumerate(cells[header_index] or [])
                    if _schedule_date(value, default_year)
                }
                if len(header_dates) >= 2:
                    for row_index in range(header_index + 1, len(cells)):
                        row = cells[row_index] or []
                        team_id = next((_schedule_team_id(value) for value in row if _schedule_team_id(value)), None)
                        if not team_id:
                            continue
                        row_cells = rows[row_index].cells if row_index < len(rows) else []
                        for column, date_value in header_dates.items():
                            value = row[column] if column < len(row) else ''
                            if _is_schedule_bye(value):
                                add_event(date_value, team_id)
                            else:
                                opponent_id = _schedule_team_id(value)
                                if opponent_id:
                                    venue = '自空' if column < len(row_cells) and _is_red_schedule_cell(page, row_cells[column]) else '山水'
                                    add_event(date_value, team_id, opponent_id, venue)
                                elif not str(value or '').strip():
                                    add_event(date_value, team_id)

                # 日付と対戦チームが同じ行にある形式にも対応する。
                for row_index, row in enumerate(cells):
                    row = row or []
                    date_values = [(column, _schedule_date(value, default_year)) for column, value in enumerate(row)]
                    date_values = [(column, date_value) for column, date_value in date_values if date_value]
                    if not date_values:
                        continue
                    team_ids = []
                    for value in row:
                        team_id = _schedule_team_id(value)
                        if team_id and team_id not in team_ids:
                            team_ids.append(team_id)
                    if len(team_ids) >= 2:
                        row_cells = rows[row_index].cells if row_index < len(rows) else []
                        venue = '自空' if any(_is_red_schedule_cell(page, cell) for cell in row_cells) else '山水'
                        for _, date_value in date_values:
                            add_event(date_value, team_ids[0], team_ids[1], venue)

            # 表の抽出に失敗した場合、1行単位で「日付 + チーム番号」の形式を読む。
            text = (page.extract_text() or '').translate(
                str.maketrans('０１２３４５６７８９', '0123456789')
            )
            for line in text.splitlines():
                date_value = _schedule_date(line, default_year)
                if not date_value:
                    continue
                without_date = re.sub(r'\d{1,2}[./月-]\d{1,2}(?:日)?(?:[./-]\d{2,4})?', ' ', line)
                team_ids = []
                for token in re.findall(r'028\d{2}|\b\d{1,2}\b', without_date):
                    team_id = _schedule_team_id(token)
                    if team_id and team_id not in team_ids:
                        team_ids.append(team_id)
                if _is_schedule_bye(line) and team_ids:
                    add_event(date_value, team_ids[0])
                else:
                    for index in range(0, len(team_ids) - 1, 2):
                        add_event(date_value, team_ids[index], team_ids[index + 1])

    events = []
    for matchup in matchups.values():
        if len(matchup['team_ids']) == 2:
            for team_id, opponent_id in (matchup['team_ids'], matchup['team_ids'][::-1]):
                events.append({
                    'team_id': team_id,
                    'date': matchup['date'],
                    'opponent_team_id': opponent_id,
                    'venue': matchup['venue']
                })
        else:
            events.append({
                'team_id': matchup['team_ids'][0],
                'date': matchup['date'],
                'opponent_team_id': None,
                'venue': ''
            })
    return sorted(events, key=lambda event: (event['date'], int(event['team_id'])))


# --- 2. PDFファイルのダウンロード ---
def download_pdf(url):
    """指定されたURLからPDFファイルをダウンロードする"""
    print(f"PDFをダウンロード中: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()
        return BytesIO(response.content)
    except requests.exceptions.RequestException as e:
        print(f"PDFのダウンロードに失敗しました: {e}")
        return None

# --- 3. PDFからのデータ抽出と整形 ---
def extract_and_process_ranking(pdf_file):
    """PDFからランキングデータを抽出し、整形する"""
    if pdf_file is None:
        return None

    ranking_data = {}

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''

            # Division Standings のポイントの行を特定する
            if "Total:" in text:
                team_num_line_start = text.find("Team #:")
                # 一部のPDFでは "Team #:" だったり "Team #:" の表記揺れがあるため両方を試す
                if team_num_line_start == -1:
                    team_num_line_start = text.find("Team #")

                total_points_line_start = text.find("Total:")

                if team_num_line_start != -1 and total_points_line_start != -1:
                    total_points_line = text[total_points_line_start :].split('\n')[0].strip()
                    team_num_line = text[team_num_line_start : total_points_line_start].split('\n')[0].strip()

                    # チーム番号とポイントを抽出
                    # Team #: 1 2 3 ... のような並びを想定
                    if 'Team #' in team_num_line:
                        valid_team_nums = [num for num in team_num_line.split('Team #')[-1].replace(':','').strip().split() if num.isdigit()]
                    else:
                        valid_team_nums = [num for num in team_num_line.split() if num.isdigit()]

                    points = total_points_line.split('Total:')[-1].strip().split()

                    if len(valid_team_nums) == len(points) and len(valid_team_nums) > 0:
                        for team_id, point in zip(valid_team_nums, points):
                            if team_id not in ranking_data:
                                try:
                                    ranking_data[team_id] = int(point)
                                except ValueError:
                                    # 整数に変換できない場合はスキップ
                                    continue

    # 抽出したデータをDataFrameに変換
    if not ranking_data:
        return pd.DataFrame(columns=['team_id', 'points'])

    df = pd.DataFrame(list(ranking_data.items()), columns=['team_id', 'points'])

    # 総合ポイントの高い順に並べ替える
    df = df.sort_values(by='points', ascending=False)

    return df


def extract_individual_stats(pdf_file, team_name_map=None):
    """個人成績PDFから個人ごとの成績を抽出する。返り値は辞書のリスト。
    各辞書: {team_name, player_name, sl, wins, avg_points, points_rate}
    team_name_map が指定された場合はそちらを優先し、なければ TEAM_NAME_MAP を使用する。
    """
    if pdf_file is None:
        return []

    effective_map = team_name_map if team_name_map else TEAM_NAME_MAP

    individuals = []

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
            for ln in lines:
                # 一行形式: Name Member# SL Gender Team TMP TMW Points MatchPoints Points% Place
                # 例: Hayato Takenaka 16997 2 M 02810 6 5 84 14.00 70.0 % 1
                normalized = re.sub(r'\s+', ' ', ln).strip()
                m = re.match(
                    r"^(?P<name>.+?)\s+"
                    r"(?P<member>\d+)\s+"
                    r"(?P<sl>\d+)\s+"
                    r"(?P<gender>[A-Za-z]+)\s+"
                    r"(?P<team>028\d{2})\s+"
                    r"(?P<tmp>\d+)\s+"
                    r"(?P<tmore>\d+)\s+"
                    r"(?P<points>\d+)\s+"
                    r"(?P<avg>\d+(?:\.\d+)?)\s+"
                    r"(?P<rate>\d+(?:\.\d+)?)\s*%?"
                    r"(?:\s+\d+)?$",
                    normalized
                )
                if m:
                    team_code = m.group('team')
                    # team_code は '02810' のようになっている -> team_id は '10'
                    team_id = team_code.replace('028','').lstrip('0') or team_code[-2:]
                    team_name = effective_map.get(team_id) or TEAM_NAME_MAP.get(team_id, f'チームNo.{team_id}')
                    player_name = m.group('name')
                    gender = m.group('gender')
                    # 性別を日本語に変換
                    gender_jp = '男' if gender.upper() == 'M' else '女' if gender.upper() == 'F' else gender
                    sl = m.group('sl')
                    tmp = m.group('tmp')
                    tmore = m.group('tmore')
                    avg = m.group('avg')
                    rate = m.group('rate')

                    individuals.append({
                        'team_name': team_name,
                        'player_name': player_name,
                        'player_number': m.group('member'),
                        'gender': gender_jp,
                        'sl': int(sl),
                        'wins': f"{tmore}/{tmp}",
                        'avg_points': float(avg),
                        'points_rate': f"{rate}%"
                    })

    return individuals


def merge_individual_stats_with_roster(parsed_individuals, roster_entries):
    """名簿を基準に個人成績をマージし、未出場メンバーも0成績で含める。"""
    if not roster_entries:
        return parsed_individuals

    def normalize_name(name):
        return re.sub(r'\s+', ' ', str(name or '')).strip().casefold()

    stats_by_number = {}
    stats_by_name = {}
    for person in parsed_individuals:
        player_number = str(person.get('player_number') or '').strip()
        if player_number:
            stats_by_number[player_number] = person

        name_key = (
            normalize_name(person.get('team_name')),
            normalize_name(person.get('player_name'))
        )
        stats_by_name.setdefault(name_key, []).append(person)

    merged = []
    matched_numbers = set()
    matched_names = set()
    consumed_person_ids = set()

    for entry in roster_entries:
        player_number = str(entry.get('player_number') or '').strip()
        roster_name_key = (
            normalize_name(entry.get('team_name')),
            normalize_name(entry.get('player_name'))
        )
        stats = stats_by_number.get(player_number)
        if stats and id(stats) in consumed_person_ids:
            stats = None
        if not stats:
            name_matches = [person for person in stats_by_name.get(roster_name_key, []) if id(person) not in consumed_person_ids]
            if len(name_matches) == 1:
                stats = name_matches[0]

        merged_person = {
            'team_name': entry['team_name'],
            'player_name': entry['player_name'],
            'player_number': player_number,
            'gender': entry['gender'],
            'sl': entry['sl'],
            'wins': '0/0',
            'avg_points': 0.0,
            'points_rate': '0%'
        }

        if stats:
            stats_player_number = str(stats.get('player_number') or '').strip()
            merged_person.update({
                'team_name': stats.get('team_name') or merged_person['team_name'],
                'player_name': stats.get('player_name') or merged_person['player_name'],
                'player_number': str(stats.get('player_number') or merged_person['player_number']),
                'gender': stats.get('gender') or merged_person['gender'],
                'sl': stats.get('sl', merged_person['sl']),
                'wins': stats.get('wins') or merged_person['wins'],
                'avg_points': stats.get('avg_points', merged_person['avg_points']),
                'points_rate': stats.get('points_rate') or merged_person['points_rate'],
            })
            if player_number:
                matched_numbers.add(player_number)
            if stats_player_number:
                matched_numbers.add(stats_player_number)
            matched_names.add(roster_name_key)
            consumed_person_ids.add(id(stats))

        merged.append(merged_person)

    for person in parsed_individuals:
        player_number = str(person.get('player_number') or '').strip()
        name_key = (
            normalize_name(person.get('team_name')),
            normalize_name(person.get('player_name'))
        )
        if (player_number and player_number in matched_numbers) or name_key in matched_names:
            continue
        if id(person) in consumed_person_ids:
            continue
        merged.append(person)

    return merged


def extract_team_roster(pdf_file):
    """チーム名簿PDFからチーム/メンバー情報だけを抽出する。
    戻り値: [{'team_id','team_name','player_name','player_number','gender','sl'}]
    """
    if pdf_file is None:
        return []

    roster = []

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            lines = text.split('\n')
            
            current_teams = {}  # {col_index: (team_code, team_name)}
            
            for ln in lines:
                ln = ln.rstrip()
                if not ln.strip():
                    continue
                
                # 3カラムレイアウトのチーム見出し行を検出
                # 例: "02801 Kangaroo Kick 02802 Oku niki 02803 Wagamama foundry"
                team_headers = list(re.finditer(r'(028\d{2})\s+(.+?)(?=\s*028\d{2}|$)', ln))
                if len(team_headers) >= 2:  # 複数チームが並んでいる
                    current_teams = {}
                    for idx, match in enumerate(team_headers):
                        team_code = match.group(1)
                        team_name = match.group(2).strip()
                        current_teams[idx] = (team_code, team_name)
                    continue
                
                # 単一チーム見出し行
                single_team_match = re.match(r'^(028\d{2})\s+(.+?)$', ln)
                if single_team_match and 'Host' not in ln:
                    team_code = single_team_match.group(1)
                    team_name = single_team_match.group(2).strip()
                    current_teams = {0: (team_code, team_name)}
                    continue
                
                # ホスト行やヘッダーはスキップ
                if re.match(r'^Host:|^SL\s+Number|^Page \d+|^N\s+SL\s+Number', ln):
                    continue
                
                if not current_teams:
                    continue
                
                # 3カラムのプレイヤー行を抽出
                # 各カラムは "N? SL * member_number Name" の形式 (Nは新規メンバーマーカー)
                # 例: "N 5 * 15428 Murayama, Shotaro N 2 * 16770 Oku, Yuki N 5 * 15343 Iwano, Atsushi"
                # 例: "6 * 15428 Murayama, Shotaro 2 * 16770 Oku, Yuki 5 * 15343 Iwano, Atsushi"
                player_blocks = list(re.finditer(r'N?\s*(\d+)\s+\*\s+(\d+)\s+([A-Za-z][\w\s,\.-]+?)(?=\s+N?\s*\d+\s+\*|$)', ln))
                
                for idx, block in enumerate(player_blocks):
                    if idx >= len(current_teams):
                        break
                    
                    sl = int(block.group(1))
                    member = block.group(2)
                    name = block.group(3).strip()
                    
                    team_code, team_name = current_teams[idx]
                    team_id = team_code.replace('028', '').lstrip('0') or team_code[-2:]
                    
                    roster.append({
                        'team_id': team_id,
                        'team_code': team_code,
                        'team_name': team_name,
                        'player_name': name,
                        'player_number': member,
                        'gender': '-',
                        'sl': sl
                    })

    return roster


def group_roster_by_team(roster_entries):
    grouped = {}
    for entry in roster_entries:
        team_id = entry.get('team_id') or ''
        team_key = str(team_id)
        if team_key not in grouped:
            grouped[team_key] = {
                'team_id': team_id,
                'team_name': entry.get('team_name', f'チームNo.{team_id}'),
                'players': []
            }
        grouped[team_key]['players'].append({
            'player_name': entry['player_name'],
            'player_number': entry['player_number'],
            'gender': entry['gender'],
            'sl': entry['sl']
        })
    # チーム名でソート
    return sorted(grouped.values(), key=lambda t: t['team_name'])


def reconcile_ranking_team_ids(ranking_df, roster_grouped):
    """ランキングを現行名簿に合わせて整える。"""
    if ranking_df is None or ranking_df.empty or not roster_grouped:
        return ranking_df

    roster_team_id_set = {str(team['team_id']) for team in roster_grouped}
    if '12' not in roster_team_id_set:
        ranking_df = ranking_df[ranking_df['team_id'].astype(str) != '12'].copy()

    ranking_team_id_set = set(ranking_df['team_id'].astype(str))
    extra_ids = ranking_team_id_set - roster_team_id_set
    missing_ids = roster_team_id_set - ranking_team_id_set

    if len(extra_ids) != 1 or len(missing_ids) != 1:
        return ranking_df.sort_values(by=['points', 'team_id'], ascending=[False, True]).reset_index(drop=True)

    print(f"名簿にないランキングIDを除外し、名簿にだけあるIDを補完します: extra={sorted(extra_ids)}, missing={sorted(missing_ids)}")
    ranking_df = ranking_df[ranking_df['team_id'].astype(str).isin(roster_team_id_set)].copy()

    existing_ids = set(ranking_df['team_id'].astype(str))
    missing_rows = [
        {'team_id': str(team['team_id']), 'points': 0}
        for team in roster_grouped
        if str(team['team_id']) not in existing_ids
    ]
    if missing_rows:
        ranking_df = pd.concat([ranking_df, pd.DataFrame(missing_rows)], ignore_index=True)

    return ranking_df.sort_values(by=['points', 'team_id'], ascending=[False, True]).reset_index(drop=True)

# --- 5. SL変動情報の抽出 ---
def extract_sl_changes():
    """SLレポートページから028ディビジョンのSL変動情報を抽出"""
    try:
        sl_report_url = "https://cue-sports.com/jpa/sl_report.php"
        print(f"SLレポートページを解析中: {sl_report_url}")
        response = requests.get(sl_report_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        sl_changes = []
        
        # 全ディビジョンのテーブルを探す（複数テーブルがある）
        tables = soup.find_all('table', class_='cp_table')
        
        for table in tables:
            rows = table.find_all('tr')[2:]  # ヘッダー行をスキップ
            
            for row in rows:
                tds = row.find_all('td')
                if len(tds) < 5:
                    continue
                
                try:
                    # テーブル構造: 名前, OLD日付, OLD SL, 矢印, NEW SL, NEW日付
                    player_link = tds[0].find('a')
                    if not player_link:
                        continue
                    
                    player_name = player_link.get_text(strip=True)
                    member_code = player_link.get('href', '').split('code=')[-1]
                    
                    old_date = tds[1].get_text(strip=True) if len(tds) > 1 else ''
                    old_sl_text = tds[2].get_text(strip=True)
                    new_sl_text = tds[4].get_text(strip=True)
                    new_date = tds[5].get_text(strip=True) if len(tds) > 5 else ''
                    
                    # 個人成績から該当プレイヤーを探してディビジョンを確認
                    # ここでは、全プレイヤーを記録し、HTMLで028のみフィルターする
                    sl_changes.append({
                        'player_name': player_name,
                        'member_number': member_code,
                        'old_sl': old_sl_text,
                        'old_date': old_date,
                        'new_sl': new_sl_text,
                        'new_date': new_date
                    })
                except (IndexError, AttributeError):
                    continue
        
        # 028ディビジョンのプレイヤーのみをフィルター
        # 既存の個人成績データから028のメンバーを取得
        individual_members = set()
        if os.path.exists(JSON_FILENAME):
            try:
                with open(JSON_FILENAME, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                    for person in existing.get('individuals', []):
                        individual_members.add(person.get('player_number'))
            except:
                pass
        
        # 028に属するメンバーのみをフィルター
        filtered_changes = [
            change for change in sl_changes 
            if change['member_number'] in individual_members
        ]
        
        return filtered_changes
    except Exception as e:
        print(f"SLレポート取得エラー: {e}")
        return []

# --- 4. メイン処理とJSON保存 ---
def main():
    # 既存の JSON を読み込み（存在すればランキングを保持）
    existing = {}
    if os.path.exists(JSON_FILENAME):
        try:
            with open(JSON_FILENAME, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    standings_soup, standings_page_url = fetch_standings_page()
    pdf_urls = find_pdf_urls(standings_soup, standings_page_url)
    latest_pdf_url = pdf_urls.get('S')
    roster_pdf_url = pdf_urls.get('R')
    schedule_pdf_url = find_schedule_pdf_url(standings_soup, standings_page_url)

    # 現在のチェック時刻（JST）
    now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y年%m月%d日 %H:%M JST')

    # ベースとなる構造を作成（既存データを引き継ぐ）
    data_to_save = {
        'last_checked': now_jst,
        'last_checked_source': latest_pdf_url or existing.get('source_pdf'),
        'last_updated': existing.get('last_updated'),
        'source_pdf': existing.get('source_pdf'),
        'individuals': existing.get('individuals', []),
        'individuals_pdf': existing.get('individuals_pdf'),
        'sl_changes': existing.get('sl_changes', []),
        'ranking': existing.get('ranking', []),
        'roster': existing.get('roster', []),
        'roster_pdf': existing.get('roster_pdf'),
        'schedule': existing.get('schedule', []),
        'schedule_pdf': existing.get('schedule_pdf'),
    }

    # 名簿PDFからチーム/メンバーを抽出
    roster_pdf_content = download_pdf(roster_pdf_url) if roster_pdf_url else None
    roster_entries = extract_team_roster(roster_pdf_content) if roster_pdf_content else []
    roster_grouped = group_roster_by_team(roster_entries) if roster_entries else []
    roster_name_map = {str(team['team_id']): team['team_name'] for team in roster_grouped}
    if roster_grouped:
        data_to_save['roster'] = roster_grouped
    if roster_pdf_url:
        data_to_save['roster_pdf'] = roster_pdf_url

    if schedule_pdf_url:
        schedule_pdf_content = download_pdf(schedule_pdf_url)
        schedule = extract_schedule(schedule_pdf_content, schedule_pdf_url)
        if schedule:
            data_to_save['schedule'] = schedule
            data_to_save['schedule_pdf'] = schedule_pdf_url

    if latest_pdf_url:
        pdf_content = download_pdf(latest_pdf_url)
        ranking_df = extract_and_process_ranking(pdf_content)

        # 個人成績PDF (P型) を解析
        p_pdf_url = pdf_urls.get('P')
        p_pdf_content = download_pdf(p_pdf_url) if p_pdf_url else None
        individuals_source_url = None
        individuals = extract_individual_stats(p_pdf_content, team_name_map=roster_name_map) if p_pdf_content else []
        if individuals:
            individuals_source_url = p_pdf_url

        if not individuals and pdf_content:
            pdf_content.seek(0)
            individuals = extract_individual_stats(pdf_content, team_name_map=roster_name_map)
            if individuals:
                individuals_source_url = latest_pdf_url

        if individuals and roster_entries:
            individuals = merge_individual_stats_with_roster(individuals, roster_entries)

        # 個人成績が取れない（新シーズン開始前）場合は名簿で補完する
        if not individuals and roster_entries:
            individuals = [{
                'team_name': e['team_name'],
                'player_name': e['player_name'],
                'player_number': e['player_number'],
                'gender': e['gender'],
                'sl': e['sl'],
                'wins': '0/0',
                'avg_points': 0.0,
                'points_rate': '0%'
            } for e in roster_entries]

        data_to_save['individuals'] = individuals
        data_to_save['individuals_pdf'] = individuals_source_url or data_to_save.get('individuals_pdf')
        
        # SL変動情報を取得
        sl_changes = extract_sl_changes()
        data_to_save['sl_changes'] = sl_changes

        ranking_built = False

        if ranking_df is not None and not ranking_df.empty:
            ranking_df['team_id'] = ranking_df['team_id'].astype(str)
            ranking_df = reconcile_ranking_team_ids(ranking_df, roster_grouped)
            
            # チーム名を補完。名簿のチーム名を優先し、なければ既存マップ。
            ranking_df['team_name'] = ranking_df['team_id'].map(lambda tid: roster_name_map.get(str(tid)) or TEAM_NAME_MAP.get(str(tid)) or f"チームNo.{tid}")

            final_ranking = ranking_df[['team_name', 'team_id', 'points']].reset_index(drop=True)

            data_to_save['last_updated'] = now_jst
            data_to_save['source_pdf'] = latest_pdf_url
            data_to_save['ranking'] = final_ranking.to_dict('records')
            ranking_built = True

            print(f"\n✅ データは '{JSON_FILENAME}' として保存されました。")
            print(final_ranking)

        # ランキングが取得できなかった場合は名簿ベースで0にする
        if not ranking_built and roster_grouped:
            fallback_ranking = [
                {'team_name': team['team_name'], 'team_id': str(team['team_id'] or ''), 'points': 0}
                for team in roster_grouped
            ]
            data_to_save['ranking'] = fallback_ranking
            data_to_save['last_updated'] = now_jst
            data_to_save['source_pdf'] = latest_pdf_url or roster_pdf_url or data_to_save.get('source_pdf')
            ranking_built = True
            print("ℹ️ ランキングは名簿ベースで0pt表示に切り替えました（シーズン未開始想定）。")

        if not ranking_built:
            # PDFは取れたが解析できなかった
            print("❌ エラー: ランキングデータを抽出できませんでした。既存データを保持します。")
    else:
        print("\n❌ 最新のPDF URLを特定できなかったため、既存データの更新はチェック時刻のみ行います。")

        # PDFが見つからなくても名簿があればランキング・個人成績を0で生成
        if roster_grouped:
            data_to_save['ranking'] = [
                {'team_name': team['team_name'], 'team_id': str(team['team_id'] or ''), 'points': 0}
                for team in roster_grouped
            ]
            data_to_save['individuals'] = [{
                'team_name': e['team_name'],
                'player_name': e['player_name'],
                'player_number': e['player_number'],
                'gender': e['gender'],
                'sl': e['sl'],
                'wins': '0/0',
                'avg_points': 0.0,
                'points_rate': '0%'
            } for e in roster_entries]
            data_to_save['last_updated'] = data_to_save['last_updated'] or now_jst

    # 最後に常に JSON を保存（チェック時刻を反映）
    try:
        with open(JSON_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"JSON の保存に失敗しました: {e}")


if __name__ == '__main__':
    main()
