# 🏇 競馬配置判定システム（縮小版）

JRA-VAN DBからエクスポートしたCSVデータを使って動作する競馬予想補助アプリ。

## 使い方

### ローカル（DB直接接続）
```bash
streamlit run app_slim.py
```

### データ更新
```
update_saturday.bat  # 土曜朝に実行
update_sunday.bat    # 日曜朝に実行
```

## CSVファイル構成
```
data/
  history.csv               # 直近2週間の過去レース結果
  entries_YYYYMMDD_VV.csv  # 当日出走馬（場コード別）
```
