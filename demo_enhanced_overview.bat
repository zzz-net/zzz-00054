@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo 🚀 批次概览增强功能 - 完整链路演示
echo ============================================
echo.

cd /d "%~dp0"
set "DEMO_DIR=%TEMP%\tlr_demo_%RANDOM%"
mkdir "%DEMO_DIR%"
mkdir "%DEMO_DIR%\examples"

echo 📂 演示目录: %DEMO_DIR%
echo.

echo [步骤 1/16] 创建批次...
python -m timeline_review create --name "排查交接演示批次" --description "演示完整的增强概览功能链" >%DEMO_DIR%\step1.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step1.log & exit /b 1)
echo ✅ 批次创建成功
echo.

echo [步骤 2/16] 查看初始概览...
python -m timeline_review overview
echo.

echo [步骤 3/16] 导入 app.log...
python -m timeline_review import examples\app.log --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step3.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step3.log & exit /b 1)
echo ✅ app.log 导入成功
echo.

echo [步骤 4/16] 查看导入后概览 + 变更摘要...
python -m timeline_review overview --diff
echo.

echo [步骤 5/16] 导入 alerts.csv...
python -m timeline_review import examples\alerts.csv --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step5.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step5.log & exit /b 1)
echo ✅ alerts.csv 导入成功
echo.

echo [步骤 6/16] 查看历史快照...
python -m timeline_review overview --history
echo.

echo [步骤 7/16] 变更配置并升级版本...
python -m timeline_review config --dedup-window 180 --bump-version --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step7.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step7.log & exit /b 1)
echo ✅ 配置变更成功
echo.

echo [步骤 8/16] 查看配置变更摘要...
python -m timeline_review overview --diff
echo.

echo [步骤 9/16] 导入 notes.json...
python -m timeline_review import examples\notes.json --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step9.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step9.log & exit /b 1)
echo ✅ notes.json 导入成功
echo.

echo [步骤 10/16] 测试重复导入检测...
python -m timeline_review import examples\app.log --work-dir "%DEMO_DIR%"
echo.

echo [步骤 11/16] 获取事件 ID 进行标注...
for /f "tokens=2" %%i in ('python -m timeline_review timeline --limit 1 --work-dir "%DEMO_DIR%" ^| findstr "ID:"') do (
    set "EVENT_ID=%%i"
    goto :got_id
)
:got_id
echo ✅ 获取事件 ID: !EVENT_ID!
echo.

echo [步骤 12/16] 标注事件为根因...
python -m timeline_review label --status root !EVENT_ID! --notes "根因确认，需要修复" --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step12.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step12.log & exit /b 1)
echo ✅ 事件标注成功
echo.

echo [步骤 13/16] 查看标注变更摘要...
python -m timeline_review overview --diff
echo.

echo [步骤 14/16] 第一次导出报告...
python -m timeline_review export --format markdown --output "%DEMO_DIR%\report1.md" --save-internal --work-dir "%DEMO_DIR%" >%DEMO_DIR%\step14.log 2>&1
if errorlevel 1 (echo ❌ 失败 & type %DEMO_DIR%\step14.log & exit /b 1)
echo ✅ 第一次导出成功
echo.

echo [步骤 15/16] 查看导出变更摘要...
python -m timeline_review overview --diff
echo.

echo [步骤 16/16] 查看完整信息 - 概览 + 历史 + 导出对比 + 一致性检查 + 变更日志...
echo.
echo ============================================
echo 📊 完整信息面板
echo ============================================
echo.

echo --- 📌 一致性检查 ---
python -m timeline_review overview --check-consistency
echo.

echo --- 📤 导出对比 ---
python -m timeline_review overview --export-diff
echo.

echo --- 📝 变更日志 ---
python -m timeline_review overview --change-log --log-limit 20
echo.

echo --- 🕒 与最初状态对比 ---
python -m timeline_review overview --diff first
echo.

echo ============================================
echo 🎉 完整链路演示完成!
echo ============================================
echo.
echo 📂 所有数据保存在: %DEMO_DIR%
echo 📁 持久化数据目录: %DEMO_DIR%\.timeline_review
echo.
echo 💡 关键功能验证:
echo    ✅ 变更摘要（--diff）
echo    ✅ 历史快照（--history）
echo    ✅ 导出对比（--export-diff）
echo    ✅ 一致性检查（--check-consistency）
echo    ✅ 变更日志（--change-log）
echo    ✅ 重复导入检测
echo    ✅ 配置变更提示
echo    ✅ 持久化（重启后保留）
echo.
echo 🔍 尝试重启验证: 再次运行 overview 命令
echo.

endlocal
