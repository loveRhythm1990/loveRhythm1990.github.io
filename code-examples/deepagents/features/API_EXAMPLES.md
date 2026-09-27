# 人机协同 API 请求示例

当 Agent 遇到需要审批的操作时，会暂停并等待用户通过 API 发送审批决定。

## API 端点

```
POST /api/approve
Content-Type: application/json
```

## 请求格式

```json
{
  "approval_id": "req_12345",
  "decision": "approved",
  "reason": "可选：审批原因或备注"
}
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `approval_id` | string | ✅ | 待审批项的 ID |
| `decision` | string | ✅ | `approved` 或 `rejected` |
| `reason` | string | ❌ | 审批原因（可选） |

---

## 实际示例

### 1️⃣ Curl（命令行）

**批准操作**

```bash
curl -X POST http://localhost:5000/api/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approval_id": "req_12345",
    "decision": "approved",
    "reason": "问题已确认，可以重启服务"
  }'
```

**拒绝操作**

```bash
curl -X POST http://localhost:5000/api/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approval_id": "req_12345",
    "decision": "rejected",
    "reason": "需要更多信息再决定"
  }'
```

---

### 2️⃣ Python

```python
import requests
import json

# 批准操作
response = requests.post(
    "http://localhost:5000/api/approve",
    json={
        "approval_id": "req_12345",
        "decision": "approved",
        "reason": "问题已确认"
    }
)

print(response.status_code)  # 200
print(response.json())  
# {
#   "status": "ok",
#   "approval_id": "req_12345",
#   "decision": "approved",
#   "message": "操作已批准"
# }
```

**完整示例（带错误处理）**

```python
import requests
from typing import Dict

def submit_approval(
    api_url: str,
    approval_id: str,
    decision: str,
    reason: str = ""
) -> Dict:
    """
    发送审批决定到 Agent
    
    Args:
        api_url: API 端点 URL
        approval_id: 待审批项 ID
        decision: "approved" 或 "rejected"
        reason: 审批原因
    
    Returns:
        API 响应字典
    """
    try:
        response = requests.post(
            api_url,
            json={
                "approval_id": approval_id,
                "decision": decision,
                "reason": reason
            },
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    
    except requests.exceptions.ConnectionError:
        print(f"❌ 连接失败：无法连接到 {api_url}")
        return {"error": "Connection failed"}
    
    except requests.exceptions.Timeout:
        print("❌ 请求超时")
        return {"error": "Timeout"}
    
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP 错误：{e.response.status_code}")
        return {"error": f"HTTP {e.response.status_code}"}

# 使用
result = submit_approval(
    api_url="http://localhost:5000/api/approve",
    approval_id="req_12345",
    decision="approved",
    reason="已验证，可以执行"
)
print(result)
```

---

### 3️⃣ JavaScript / Fetch API

```javascript
// 批准操作
fetch('http://localhost:5000/api/approve', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        approval_id: 'req_12345',
        decision: 'approved',
        reason: '问题已确认'
    })
})
.then(response => {
    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }
    return response.json();
})
.then(data => {
    console.log('✅ 审批成功:', data);
})
.catch(error => {
    console.error('❌ 错误:', error);
});
```

**完整示例（async/await）**

```javascript
async function submitApproval(approvalId, decision, reason = '') {
    try {
        const response = await fetch('http://localhost:5000/api/approve', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                approval_id: approvalId,
                decision: decision,
                reason: reason
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log('✅ 审批已提交:', data);
        return data;

    } catch (error) {
        console.error('❌ 提交失败:', error);
        return null;
    }
}

// 使用
submitApproval('req_12345', 'approved', '已验证');
```

---

### 4️⃣ Node.js (axios)

```javascript
const axios = require('axios');

async function approveOperation() {
    try {
        const response = await axios.post(
            'http://localhost:5000/api/approve',
            {
                approval_id: 'req_12345',
                decision: 'approved',
                reason: '问题已确认'
            },
            {
                headers: {
                    'Content-Type': 'application/json'
                },
                timeout: 5000
            }
        );

        console.log('✅ 响应:', response.data);
        return response.data;

    } catch (error) {
        if (error.response) {
            console.error('❌ 服务器错误:', error.response.status);
        } else if (error.request) {
            console.error('❌ 连接失败');
        } else {
            console.error('❌ 错误:', error.message);
        }
    }
}

approveOperation();
```

---

### 5️⃣ Go

```go
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
    "time"
)

type ApprovalRequest struct {
    ApprovalID string `json:"approval_id"`
    Decision   string `json:"decision"`
    Reason     string `json:"reason,omitempty"`
}

type ApprovalResponse struct {
    Status     string `json:"status"`
    ApprovalID string `json:"approval_id"`
    Decision   string `json:"decision"`
    Message    string `json:"message"`
}

func submitApproval(apiURL string, req ApprovalRequest) (*ApprovalResponse, error) {
    // 序列化请求
    body, err := json.Marshal(req)
    if err != nil {
        return nil, err
    }

    // 发送 POST 请求
    httpReq, err := http.NewRequest(
        "POST",
        apiURL,
        bytes.NewBuffer(body),
    )
    if err != nil {
        return nil, err
    }

    httpReq.Header.Set("Content-Type", "application/json")

    client := &http.Client{
        Timeout: 5 * time.Second,
    }

    resp, err := client.Do(httpReq)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    // 解析响应
    var result ApprovalResponse
    if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
        return nil, err
    }

    return &result, nil
}

func main() {
    response, err := submitApproval(
        "http://localhost:5000/api/approve",
        ApprovalRequest{
            ApprovalID: "req_12345",
            Decision:   "approved",
            Reason:     "问题已确认",
        },
    )

    if err != nil {
        fmt.Printf("❌ 错误: %v\n", err)
        return
    }

    fmt.Printf("✅ 响应: %+v\n", response)
}
```

---

## 响应格式

**成功响应（200 OK）**

```json
{
  "status": "ok",
  "approval_id": "req_12345",
  "decision": "approved",
  "message": "操作已批准"
}
```

**错误响应（400 Bad Request）**

```json
{
  "error": "invalid_approval_id",
  "message": "审批 ID 不存在"
}
```

---

## 工作流程

```
用户界面
   ↓
发送 API 请求
POST /api/approve
{
  "approval_id": "req_12345",
  "decision": "approved"
}
   ↓
后台 Agent
接收批准
继续执行
   ↓
返回结果
```

### 时间线

1. **T0**：Agent 遇到需要审批的操作，暂停并保存到数据库
2. **T0+1s**：通知用户（邮件/UI/Slack）
3. **T0+5m**：用户查看审批请求
4. **T0+6m**：用户通过 API 发送决定
5. **T0+6.1m**：Agent 收到批准，恢复执行
6. **T0+8m**：Agent 完成操作，返回结果

---

## 生产环境最佳实践

### 1️⃣ 添加身份验证

```bash
curl -X POST http://localhost:5000/api/approve \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{...}'
```

### 2️⃣ 添加日志

```python
@app.route('/api/approve', methods=['POST'])
def approve_action():
    approval_id = request.json['approval_id']
    decision = request.json['decision']
    
    logger.info(
        f"审批决定",
        extra={
            "approval_id": approval_id,
            "decision": decision,
            "timestamp": datetime.now()
        }
    )
    
    # ... 处理逻辑
```

### 3️⃣ 添加超时

```python
@app.route('/api/approve', methods=['POST'])
def approve_action():
    approval_id = request.json['approval_id']
    
    approval = pending_approvals.get(approval_id)
    if not approval:
        return {"error": "approval_not_found"}, 404
    
    # 检查是否已超时（如 1 小时）
    age = time.time() - approval['timestamp']
    if age > 3600:
        return {"error": "approval_expired"}, 410
    
    # ... 处理逻辑
```

### 4️⃣ 添加审计日志

```python
def log_approval_decision(approval_id, decision, user_id, reason=""):
    audit_log = {
        "approval_id": approval_id,
        "decision": decision,
        "user_id": user_id,
        "reason": reason,
        "timestamp": datetime.now().isoformat(),
        "ip_address": request.remote_addr
    }
    db.audit_logs.insert_one(audit_log)
```

---

## 常见错误

### ❌ 错误 1：approval_id 不存在

**问题**：发送了无效的 approval_id

**解决**：
```python
# 先获取待审批列表
@app.route('/api/pending-approvals', methods=['GET'])
def get_pending():
    return {
        'pending': [
            {
                'approval_id': 'req_12345',
                'operation': 'restart_service',
                'created_at': '2026-09-27T10:00:00Z'
            }
        ]
    }

# 然后才批准
requests.post('/api/approve', json={
    'approval_id': 'req_12345',
    'decision': 'approved'
})
```

### ❌ 错误 2：连接超时

**问题**：API 服务未启动或地址错误

**解决**：
```bash
# 检查服务是否运行
curl -v http://localhost:5000/api/approve

# 检查防火墙
sudo lsof -i :5000

# 检查进程
ps aux | grep python
```

### ❌ 错误 3：JSON 格式错误

**问题**：JSON 格式不正确

**解决**：
```bash
# ❌ 错误
curl -X POST ... -d '{approval_id: "req_12345"}'

# ✅ 正确
curl -X POST ... -d '{"approval_id": "req_12345"}'
```

---

## 集成建议

### 选项 1：前端 Web UI

```javascript
// React 组件示例
function ApprovalButton({ approvalId, onApprove }) {
    const [loading, setLoading] = useState(false);

    const handleApprove = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    approval_id: approvalId,
                    decision: 'approved',
                    reason: 'User approved from UI'
                })
            });
            const data = await response.json();
            onApprove(data);
        } finally {
            setLoading(false);
        }
    };

    return (
        <button onClick={handleApprove} disabled={loading}>
            {loading ? '提交中...' : '批准'}
        </button>
    );
}
```

### 选项 2：CLI 工具

```bash
#!/bin/bash
# approve.sh - 命令行审批工具

approval_id=$1
decision=$2

curl -X POST http://localhost:5000/api/approve \
  -H "Content-Type: application/json" \
  -d "{
    \"approval_id\": \"$approval_id\",
    \"decision\": \"$decision\"
  }"
```

使用：
```bash
./approve.sh req_12345 approved
```

### 选项 3：Slack Bot

```python
from slack_bolt import App

app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.action("approve_button")
def approve_action(ack, action, respond):
    ack()
    
    approval_id = action["value"]
    
    response = requests.post(
        "http://localhost:5000/api/approve",
        json={
            "approval_id": approval_id,
            "decision": "approved",
            "reason": "Approved from Slack"
        }
    )
    
    respond(f"✅ 已批准：{approval_id}")

app.start()
```

---

**记住**：API 请求必须包含有效的 `approval_id` 和 `decision`！ 🔐
