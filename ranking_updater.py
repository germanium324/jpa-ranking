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

# --- 設定値 ---
BASE_URL = "http://www.poolplayers.jp"
STANDINGS_URL = f"{BASE_URL}/standings/"
TARGET_DIVISION_NAME = "028 COLLEGE (TUE)"
JSON_FILENAME = 'ranking_data.json'

# チーム名マッピング辞書（PDFからはチームIDしか取れないため、手動で用意）
TEAM_NAME_MAP = {
    '1': 'Kangaroo Kick', '2': 'Oku niki', '3': 'Wagamama foundry', '4': 'Hinerisugita!',
    '5': 'Tokyo Bayashis', '6': 'Lucky Flockers', '7': 'Taigaku Straight Flush', '8': 'Happy Lucky Ricky',
    '9': 'Rui X sei', '10': 'yattenna', '11': 'Tamanchu'
}

# --- 1. 最新のPDF URLを特定 ---
def find_latest_pdf_url(standings_url):
    """スタンディングページを解析し、最新の028ディビジョンのスコアシートPDFのURLを特定する"""
    print(f"スタンディングページを解析中: {standings_url}")
    try:
        response = requests.get(standings_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        target_cell = soup.find(text=TARGET_DIVISION_NAME)
        if not target_cell:
            print(f"エラー: ディビジョン名 '{TARGET_DIVISION_NAME}' がページに見つかりません。")
            return None

        target_row = target_cell.find_parent('tr')
        if not target_row:
            return None

        all_links_in_row = target_row.find_all('a')

        # Division コードを取り出す（例: '028'）
        division_code = TARGET_DIVISION_NAME.split()[0]

        # S型PDFの候補を抽出し、ファイル名の数字部分で最新（最大）を選ぶ
        import re
        candidates = []
        for a in all_links_in_row:
            href = a.get('href')
            if not href:
                continue
            m = re.search(rf'S{division_code}(\d+)\.pdf', href)
            if m:
                num = int(m.group(1))
                url = href if href.startswith('http') else BASE_URL + href
                candidates.append((num, url))

        if candidates:
            candidates.sort(reverse=True)
            latest = candidates[0][1]
            print(f"最新の（Standings）PDF URLを特定しました: {latest}")
            return latest

        # フォールバック: 以前のように行内の最初のPDFリンクを返す
        for a in all_links_in_row:
            href = a.get('href')
            if not href:
                continue
            full_url = href if href.startswith('http') else BASE_URL + href
            if full_url.lower().endswith('.pdf'):
                print(f"フォールバックでPDF URLを特定しました: {full_url}")
                return full_url

        return None

    except requests.exceptions.RequestException as e:
        print(f"スタンディングページの取得に失敗しました: {e}")
        return None


def find_pdf_url_by_type(standings_url, type_char='P'):
    """指定タイプ（'P','S'など）のPDF URLを同じ行から探し、最新（ファイル名の数字が最大）を返す。"""
    try:
        response = requests.get(standings_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        target_cell = soup.find(text=TARGET_DIVISION_NAME)
        if not target_cell:
            return None
        target_row = target_cell.find_parent('tr')
        if not target_row:
            return None

        all_links = target_row.find_all('a')
        division_code = TARGET_DIVISION_NAME.split()[0]
        # P型PDFの候補を抽出
        candidates = []
        for a in all_links:
            href = a.get('href')
            if not href:
                continue
            # 例: /standings/028/P028111925.pdf
            m = re.search(rf'{type_char}{division_code}(\d+).pdf', href)
            if m:
                num = int(m.group(1))
                url = href if href.startswith('http') else BASE_URL + href
                candidates.append((num, url))
        if not candidates:
            return None
        # 数字が最大のもの（最新）を選ぶ
        candidates.sort(reverse=True)
        return candidates[0][1]
    except requests.exceptions.RequestException:
        return None

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


def extract_individual_stats(pdf_file):
    """個人成績PDFから個人ごとの成績を抽出する。返り値は辞書のリスト。
    各辞書: {team_name, player_name, sl, wins, avg_points, points_rate}
    """
    if pdf_file is None:
        return []

    individuals = []

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
            for ln in lines:
                # 一行形式: Name Member# SL Gender Team TMP TMW Points MatchPoints Points% Place
                # 例: Hayato Takenaka 16997 2 M 02810 6 5 84 14.00 70.0 % 1
                m = re.match(r"^(?P<name>.+?)\s+(?P<member>\d+)\s+(?P<sl>\d+)\s+(?P<gender>\w+)\s+(?P<team>028\d+)\s+(?P<tmp>\d+)\s+(?P<tmore>\d+)\s+(?P<points>\d+)\s+(?P<avg>[0-9.]+)\s+(?P<rate>[0-9.]+)\s*%?", ln)
                if m:
                    team_code = m.group('team')
                    # team_code は '02810' のようになっている -> team_id は '10'
                    team_id = team_code.replace('028','').lstrip('0') or team_code[-2:]
                    team_name = TEAM_NAME_MAP.get(team_id, f'チームNo.{team_id}')
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
                team_headers = list(re.finditer(r'(028\d{2})\s+([A-Za-z][^0-9]+?)(?=\s*028\d{2}|$)', ln))
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
                # 各カラムは "N SL *member_number Name" の形式
                # 例: "N 5 * 15428 Murayama, Shotaro N 2 * 16770 Oku, Yuki N 5 * 15343 Iwano, Atsushi"
                # 名前に 'N' が含まれる可能性があるため、より正確なパターンを使用
                player_blocks = list(re.finditer(r'N\s+(\d+)\s+\*\s*(\d+)\s+(.+?)(?=\s+N\s+\d+\s+\*|$)', ln))
                
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

    latest_pdf_url = find_latest_pdf_url(STANDINGS_URL)
    roster_pdf_url = find_pdf_url_by_type(STANDINGS_URL, type_char='R')

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

    if latest_pdf_url:
        pdf_content = download_pdf(latest_pdf_url)
        ranking_df = extract_and_process_ranking(pdf_content)

        # 個人成績PDF (P型) を同じ行から探して解析
        p_pdf_url = find_pdf_url_by_type(STANDINGS_URL, type_char='P')
        p_pdf_content = download_pdf(p_pdf_url) if p_pdf_url else None
        individuals_from_roster = False
        individuals = extract_individual_stats(p_pdf_content) if p_pdf_content else []

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
            individuals_from_roster = True

        data_to_save['individuals'] = individuals
        data_to_save['individuals_pdf'] = p_pdf_url or roster_pdf_url or data_to_save.get('individuals_pdf')
        
        # SL変動情報を取得
        sl_changes = extract_sl_changes()
        data_to_save['sl_changes'] = sl_changes

        ranking_built = False

        if ranking_df is not None and not ranking_df.empty and not individuals_from_roster:
            # チーム名を補完（試合結果が取れている場合のみ）。名簿のチーム名を優先し、なければ既存マップ。
            ranking_df['team_id'] = ranking_df['team_id'].astype(str)
            ranking_df['team_name'] = ranking_df['team_id'].map(lambda tid: roster_name_map.get(str(tid)) or TEAM_NAME_MAP.get(str(tid)) or f"チームNo.{tid}")

            final_ranking = ranking_df[['team_name', 'team_id', 'points']].reset_index(drop=True)

            data_to_save['last_updated'] = now_jst
            data_to_save['source_pdf'] = latest_pdf_url
            data_to_save['ranking'] = final_ranking.to_dict('records')
            ranking_built = True

            print(f"\n✅ データは '{JSON_FILENAME}' として保存されました。")
            print(final_ranking)

        # 個人成績が名簿由来 = シーズン未開始と判断し、ランキングも名簿ベースで0にする
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
