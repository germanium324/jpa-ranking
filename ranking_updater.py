#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import pdfplumber
import pandas as pd
from io import BytesIO
import datetime
import json
import os

# --- 設定値 ---
BASE_URL = "http://www.poolplayers.jp"
STANDINGS_URL = f"{BASE_URL}/standings/"
TARGET_DIVISION_NAME = "028 COLLEGE (TUE)"
JSON_FILENAME = 'ranking_data.json'

# チーム名マッピング辞書（PDFからはチームIDしか取れないため、手動で用意）
TEAM_NAME_MAP = {
    '1': 'Rui Q sei', '2': 'Tsukuyomi', '3': 'Sour Grapes', '4': 'Domannaka shot dan',
    '5': 'Hori Masaki', '6': 'Wagamama Factory', '7': 'Gold D Ricky', '8': 'Bayashis',
    '9': 'Crab Sand', '10': 'Tamanchu', '11': 'Candy Qune', '12': 'Hiyokogumi'
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

        # 2番目のリンク（インデックス1）がスコアシートPDFのURLであると仮定
        if len(all_links_in_row) >= 2:
            scoresheet_link = all_links_in_row[1].get('href')

            if not scoresheet_link:
                return None

            if scoresheet_link.startswith('/'):
                full_url = BASE_URL + scoresheet_link
            else:
                full_url = scoresheet_link

            if full_url.lower().endswith('.pdf'):
                print(f"最新のPDF URLを特定しました: {full_url}")
                return full_url

        return None

    except requests.exceptions.RequestException as e:
        print(f"スタンディングページの取得に失敗しました: {e}")
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

# --- 4. メイン処理とJSON保存 ---
def main():
    latest_pdf_url = find_latest_pdf_url(STANDINGS_URL)

    if latest_pdf_url:
        pdf_content = download_pdf(latest_pdf_url)
        ranking_df = extract_and_process_ranking(pdf_content)

        if ranking_df is not None and not ranking_df.empty:

            # チーム名を補完
            ranking_df['team_id'] = ranking_df['team_id'].astype(str)
            ranking_df['team_name'] = ranking_df['team_id'].map(TEAM_NAME_MAP)
            # マッピングがない場合はフォールバックのチーム名を入れる
            ranking_df['team_name'] = ranking_df.apply(
                lambda row: TEAM_NAME_MAP.get(str(row['team_id']), f"チームNo.{row['team_id']}"), axis=1
            )

            # 最終的なランキング表
            final_ranking = ranking_df[['team_name', 'team_id', 'points']].reset_index(drop=True)

            # JSONとして保存するデータ構造を作成
            data_to_save = {
                'last_updated': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y年%m月%d日 %H:%M JST'),
                'source_pdf': latest_pdf_url,
                'ranking': final_ranking.to_dict('records')
            }

            # JSONファイルとして保存
            with open(JSON_FILENAME, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)

            print(f"\n✅ データは '{JSON_FILENAME}' として保存されました。")
            print(final_ranking)
        else:
            print("❌ エラー: ランキングデータを抽出できませんでした。")
    else:
        print("\n❌ 最新のPDF URLを特定できなかったため、処理を中断しました。")


if __name__ == '__main__':
    main()
