"""
Open Digital Foundation SDK 使用示例
"""

# 初始化客户端
from open_foundation import foundationClient

# 使用API Key
client = foundationClient(base_url="http://localhost:8001", api_key="your-api-key")

# 或使用用户名密码登录
client = foundationClient(base_url="http://localhost:8001")
client.login("username", "password")

# 项目管理
projects = client.projects.list()
print(projects)

project = client.projects.create(
    name="我的AI项目", description="这是一个测试项目", project_type="custom"
)

# 数据集管理
datasets = client.datasets.list()
dataset = client.datasets.create(name="训练数据集", data_type="tabular")

# 模型训练
job = client.training.create_job(
    name="模型训练任务", dataset_id=1, model_type="random_forest"
)

# 在线推理
result = client.deployment.inference(deployment_id=1, data=[[1.0, 2.0, 3.0]])
print(result)
