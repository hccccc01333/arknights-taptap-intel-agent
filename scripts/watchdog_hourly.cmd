@echo off
rem 看门狗小时级兜底：常驻看门狗死了也有人管（幂等，自身带重启冷却）
cd /d "D:\AI数据分析\明日方舟舆情日周保分析平台"
python L1_data_source\watchdog.py --check >> data\logs\watchdog_hourly.log 2>&1
