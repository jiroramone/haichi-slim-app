@echo off
chcp 65001 > nul
echo ============================================
echo  初回セットアップ: Git / GitHub 設定
echo ============================================

:: Git ユーザー設定（初回のみ）
echo.
echo [1/3] Git ユーザー設定...
git config --global user.name "jiroramone"
git config --global user.email "keitarou19921024@gmail.com"

:: リポジトリ初期化
echo.
echo [2/3] Gitリポジトリ初期化...
cd /d "%~dp0"
git init
git add .
git commit -m "Initial commit"

:: GitHub リモート追加
echo.
echo [3/3] GitHub リモート設定...
git remote add origin https://github.com/jiroramone/keiba-slim.git
git branch -M main
git push -u origin main

echo.
echo ============================================
echo 初回セットアップ完了！
echo 次回からは update_saturday.bat / update_sunday.bat を使用
echo ============================================
pause
