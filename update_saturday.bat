@echo off
chcp 65001 > nul
echo ============================================
echo  競馬データ 土曜日 更新バッチ
echo  %date% %time%
echo ============================================

:: ── Step 1: mykeibadb でDBを更新 ────────────────────────────
echo.
echo [1/4] mykeibadb 起動中...
"C:\Users\keita\Desktop\mykeibadb_v4.3\wmykeibadb.exe"
echo [1/4] mykeibadb 完了
timeout /t 5 /nobreak > nul

:: ── Step 2: CSVエクスポート ──────────────────────────────────
echo.
echo [2/4] CSVエクスポート中...
cd /d "%~dp0"
python export_csv.py
if %errorlevel% neq 0 (
    echo [ERROR] CSVエクスポート失敗
    pause
    exit /b 1
)
echo [2/4] CSVエクスポート完了

:: ── Step 3: Git コミット＆プッシュ ──────────────────────────
echo.
echo [3/4] GitHub にプッシュ中...
git add data/
git commit -m "Update race data %date:/=-%"
git push
if %errorlevel% neq 0 (
    echo [ERROR] git push 失敗
    pause
    exit /b 1
)
echo [3/4] GitHub プッシュ完了

:: ── Step 4: 完了通知 ────────────────────────────────────────
echo.
echo [4/4] 完了！
echo Streamlit Cloud が自動更新されます（1〜2分後）
echo https://jiroramone-keiba-slim.streamlit.app
echo ============================================
pause
