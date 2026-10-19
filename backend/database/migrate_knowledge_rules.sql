-- Migration: Create knowledge_rules table
-- Run: psql -d open_digital_foundation -f backend/database/migrate_knowledge_rules.sql

CREATE TABLE IF NOT EXISTS knowledge_rules (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(128) NOT NULL,
    rule VARCHAR(256) NOT NULL,
    cause VARCHAR(512),
    action VARCHAR(512),
    confidence DOUBLE PRECISION DEFAULT 0.8,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_rules_domain ON knowledge_rules(domain);

-- Seed initial industrial knowledge rules
INSERT INTO knowledge_rules (domain, rule, cause, action, confidence) VALUES
    ('生产过程温度控制', '目标温度偏高', '加热参数设置偏高', '降低设定温度并复核控制参数', 0.92),
    ('生产过程温度控制', '目标温度偏低', '加热能力不足', '提高设定温度并检查设备状态', 0.88),
    ('生产过程温度控制', '温度分布异常', '设备运行状态不均衡', '检查各区域运行参数并进行校准', 0.85),
    ('生产设备操作', '设备压力异常升高', '流道阻力增加或设备运行异常', '检查管路状态并按规程调整运行参数', 0.90),
    ('生产设备操作', '过程指标偏离目标', '设备参数或输入条件发生变化', '核查输入条件并校准设备参数', 0.87),
    ('生产设备操作', '运行指标波动', '输入或运行参数不稳定', '检查输入质量并稳定运行参数', 0.83),
    ('除尘系统', '压差超限', '滤袋堵塞或清灰系统故障', '启动强力清灰, 检查脉冲阀和压缩空气压力', 0.95),
    ('除尘系统', '粉尘排放超标', '滤袋破损或密封不良', '停机检查滤袋, 更换破损滤袋, 检查密封条', 0.93),
    ('除尘系统', '风机能耗偏高', '系统阻力增大或风机效率下降', '优化风门开度, 检查叶轮磨损, 调整变频频率', 0.86),
    ('能源调度', '峰时电价高', '当前处于电力高峰时段(8:00-11:00,18:00-21:00)', '降低可调负荷, 启动储能放电, 优先使用自发电', 0.96),
    ('能源调度', '煤气管网压力波动', '产消不平衡导致压力波动', '调节煤气柜存储量, 调整发电机组负荷, 必要时启动放散', 0.89),
    ('能源调度', '蒸汽管网热损失大', '保温层破损或疏水阀故障', '检查管网保温, 更换故障疏水阀, 优化蒸汽输送压力', 0.84)
ON CONFLICT DO NOTHING;
