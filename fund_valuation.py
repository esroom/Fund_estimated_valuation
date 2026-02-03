import urllib.request
import json
import re
import time
import csv
import os

# 尝试导入 dashscope (阿里云百炼 SDK)
try:
    import dashscope
    from dashscope import Generation
except ImportError:
    dashscope = None

# ==========================================
# 🔑 请在此处填写您的阿里云百炼 API Key
# 申请地址: https://bailian.console.aliyun.com/
ALIYUN_API_KEY = "sk-***" 
# ==========================================

def get_sina_fund_valuation(fund_code):
    """
    Fetch real-time valuation estimate for a given fund from Sina Finance API.
    Returns a dict compatible with the Eastmoney format:
    {'gsz': '...', 'gszzl': '...', 'gztime': '...'}
    """
    url = f"http://hq.sinajs.cn/list=fu_{fund_code}"
    headers = {
        "Referer": "http://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            # Sina usually uses GBK for stock/fund names
            data = response.read().decode('gbk', errors='ignore')
            # Format: var hq_str_fu_110011="Name,Time,EstValue,YestNAV,AccNAV,EstChg,EstChgPct,Date,...";
            # Example: ...="Name,14:21:00,5.4200,5.3658,7.1558,0.0942,1.0101,2026-02-03";
            
            match = re.search(r'="(.*?)";', data)
            if match:
                content = match.group(1)
                parts = content.split(',')
                if len(parts) > 7:
                    # Index 2: Estimated Value (gsz)
                    gsz = parts[2]
                    # Index 6: Estimated Change Percent (gszzl) - e.g. 1.0101 (% value)
                    gszzl = parts[6]
                    # Index 1: Time
                    gztime = parts[1]
                    
                    return {
                        'name': parts[0], 
                        'gsz': gsz,
                        'gszzl': gszzl,
                        'gztime': gztime,
                        'source': 'sina'
                    }
    except Exception as e:
        # print(f"Error fetching Sina data for {fund_code}: {e}")
        pass
    return None

def get_fund_valuation(fund_code):
    """
    Fetch real-time valuation estimate for a given fund code.
    Tries both Eastmoney and Sina, and averages the results if possible.
    """
    # 1. Fetch from Eastmoney
    em_data = None
    url_em = f"http://fundgz.1234567.com.cn/js/{fund_code}.js?rt={int(time.time()*1000)}"
    headers_em = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        req = urllib.request.Request(url_em, headers=headers_em)
        with urllib.request.urlopen(req) as response:
            data = response.read().decode('utf-8')
            match = re.search(r'jsonpgz\((.*?)\);', data)
            if match:
                json_str = match.group(1)
                em_data = json.loads(json_str)
                em_data['source'] = 'eastmoney'
    except Exception as e:
        pass

    # 2. Fetch from Sina
    sina_data = get_sina_fund_valuation(fund_code)

    # 3. Merge Strategies
    if em_data and sina_data:
        try:
            # Average the 'gszzl' (Growth Rate)
            em_rate = float(em_data.get('gszzl', 0))
            sina_rate = float(sina_data.get('gszzl', 0))
            avg_rate = (em_rate + sina_rate) / 2.0
            
            # Average the 'gsz' (Estimated Value)
            em_val = float(em_data.get('gsz', 0))
            sina_val = float(sina_data.get('gsz', 0))
            avg_val = (em_val + sina_val) / 2.0

            merged = em_data.copy()
            merged['gszzl'] = f"{avg_rate:.2f}"
            merged['gsz'] = f"{avg_val:.4f}"
            merged['source'] = 'avg(eastmoney, sina)'
            
            return merged
        except ValueError:
            return em_data 

    if em_data:
        return em_data
    
    if sina_data:
        return sina_data

    return None

def load_funds_from_csv(filename="funds.csv"):
    funds = []
    if not os.path.exists(filename):
        print(f"❌ 找不到文件: {filename}")
        print("请在同级目录下创建 funds.csv，格式如下：")
        print("code,amount,total_position,HPR")
        print("001186,10000,20000,-5.5")
        return []

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            # Skip header if it exists
            header = next(reader, None)
            
            # Simple check if the first row looks like a header (not digits)
            # If the user didn't include a header, we might need to handle that, 
            # but standard csv usually has headers. Let's assume header exists if first cell is not digit.
            if header and header[0].strip().isdigit(): 
                # actually data, no header
                 f.seek(0)
                 reader = csv.reader(f)

            for row in reader:
                if len(row) < 2:
                    continue
                
                code = row[0].strip()
                amount_str = row[1].strip()
                
                # Basic validation
                if not code or not amount_str:
                    continue
                    
                try:
                    amount = float(amount_str)
                    
                    # Try to read extra columns: total_position, HPR
                    total_position = 0.0
                    hpr = 0.0
                    
                    if len(row) > 2 and row[2].strip():
                        total_position = float(row[2].strip())
                    if len(row) > 3 and row[3].strip():
                        hpr = float(row[3].strip())
                    
                    funds.append({
                        'code': code, 
                        'amount': amount,
                        'total_position': total_position,
                        'hpr': hpr
                    })
                except ValueError:
                    print(f"⚠️ 跳过无效行: {row} (数值格式错误)")
                    
    except Exception as e:
        print(f"❌ 读取 CSV 文件出错: {e}")
        return []
        
    return funds

def analyze_with_ai(funds_data):
    """
    使用阿里云百炼大模型分析持仓并给出建议
    """
    if not funds_data:
        return

    print("\n=========== 🧠 AI 智能分析 (基于阿里云百炼) ===========")

    if not dashscope:
        print("❌ 未检测到 dashscope 库。")
        print("请在终端运行以下命令安装，然后重试：")
        print("pip install dashscope")
        return
    
    if "PLACEHOLDER" in ALIYUN_API_KEY or not ALIYUN_API_KEY:
        print("❌ 未配置 API Key。")
        print("请打开代码文件，在 'ALIYUN_API_KEY' 变量中填入您的 Key。")
        return

    dashscope.api_key = ALIYUN_API_KEY
    
    print("🤖 正在调用通义千问模型分析当前持仓与市场情绪... (请稍候)")

    # 获取当前时间
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # 构建提示词
    summary = f"当前时间: {current_time_str}\n我持有的基金今日实时估值表现如下：\n"
    for f in funds_data:
        # Calculate usage info if available
        position_info = ""
        if f.get('total_position', 0) > 0:
             usage_pct = (f['amount'] / f['total_position']) * 100
             position_info = f", 仓位已用 {usage_pct:.1f}% (目标 {f['total_position']})"
        
        summary += f"- 基金[{f['code']}] {f['name']}: 今日估值涨跌 {f['rate']}%, 估算盈亏 {f['profit']:.2f}元, 当前持有收益率 {f['hpr']}%{position_info}\n"
    
    prompt = (
        f"{summary}\n"
        "请作为一位专业的基金理财顾问，结合我的仓位使用情况和当前持有收益率，完成以下任务：\n"
        "1. 根据上述基金的单日涨跌幅，分析今天的市场板块大致走势。\n"
        "2. 针对我的持仓表现，结合**当前收益率(HPR)**和**仓位控制情况**，给出具体操作建议：\n"
        "   - 如果亏损较大且仓位较低，是否建议逢低补仓？\n"
        "   - 如果盈利较多或仓位已高，是否建议止盈或持有？\n"
        "3. 语言风格要客观、专业但通俗易懂。\n"
        "注意：请明确说明这些只是基于单日估值的参考建议，不构成绝对的投资依据。"
    )

    try:
        # 使用通义千问-Plus 模型 (qwen-plus)
        messages = [{'role': 'system', 'content': '你是一个专业的金融投资助手。'},
                    {'role': 'user', 'content': prompt}]
        
        response = Generation.call(model="qwen-plus", messages=messages)
        
        if response.status_code == 200:
            print("-" * 50)
            print(response.output.text)
            print("-" * 50)
        else:
            print(f"❌ AI 请求失败: {response.code} - {response.message}")
            
    except Exception as e:
        print(f"❌ AI 分析发生错误: {e}")


def main():
    print("\n=========== 支付宝基金实时估值助手 (CSV版) ===========")
    
    csv_file = "funds.csv"
    print(f"正在读取 {csv_file} ...")
    
    funds = load_funds_from_csv(csv_file)
            
    if not funds:
        print("未找到有效的基金数据，程序退出。")
        return

    print(f"成功加载 {len(funds)} 只基金。")
    print("\n正在查询数据，请稍候...\n")

    print(f"{'代码':<8} {'基金名称':<20} {'持有金额':<12} {'估算涨跌幅':<12} {'估算盈亏':<12} {'更新时间':<18}")
    print("-" * 90)

    total_profit = 0
    total_amount = 0
    
    # Store data for AI analysis
    funds_for_ai = []
    
    # ANSI escape codes for colors (may not work in all Windows terminals, but works in VS Code)
    RED = '\033[91m'
    GREEN = '\033[92m'
    RESET = '\033[0m'

    for fund in funds:
        data = get_fund_valuation(fund['code'])
        
        code = fund['code']
        amount = fund['amount']
        
        if data:
            name = data.get('name', '未知基金')
            # Truncate name if too long for display
            display_name = (name[:10] + '..') if len(name) > 12 else name
            
            try:
                gszzl_str = data.get('gszzl', '0')
                gszzl = float(gszzl_str) # Estimated growth rate %
                gztime = data.get('gztime', '--:--')
                
                # Calculate estimated profit for this fund
                # Profit = Amount * (Rate / 100)
                profit = amount * (gszzl / 100)
                
                total_profit += profit
                total_amount += amount
                
                # Collect data for AI
                funds_for_ai.append({
                    'code': code,
                    'name': name,
                    'rate': gszzl_str,
                    'profit': profit,
                    'amount': amount,
                    'total_position': fund.get('total_position', 0),
                    'hpr': fund.get('hpr', 0)
                })

                # Color formatting
                color = RED if gszzl >= 0 else GREEN
                sign = "+" if gszzl >= 0 else ""
                
                print(f"{code:<8} {display_name:<20} {amount:<12.2f} {color}{sign}{gszzl_str}%{RESET:<8} {color}{sign}{profit:<10.2f}{RESET} {gztime:<18}")
                
            except ValueError:
                print(f"{code:<8} {display_name:<20} {amount:<12.2f} {'数据错误':<12} {'0.00':<12} {'--':<18}")
        else:
            print(f"{code:<8} {'网络/代码错误':<20} {amount:<12.2f} {'--':<12} {'0.00':<12} {'--':<18}")

    print("-" * 90)
    
    # Call AI Analysis
    analyze_with_ai(funds_for_ai)
    
    
    # Total Summary
    total_color = RED if total_profit >= 0 else GREEN
    total_sign = "+" if total_profit >= 0 else ""
    
    print(f"💰 总持有金额: {total_amount:.2f}")
    print(f"📊 总估算盈亏: {total_color}{total_sign}{total_profit:.2f}{RESET}")
    print("=" * 90)
    print("注意: 数据来源为天天基金网估值，仅供参考，实际净值以基金公司公布为准。")

if __name__ == "__main__":
    main()
