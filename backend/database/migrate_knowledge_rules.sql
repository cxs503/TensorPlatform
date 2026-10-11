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
    ('生产过程温度控制', '终点温度偏高', '焦粉配比过高或料层偏薄', '降低焦粉比例0.2%或增厚料层5mm', 0.92),
    ('生产过程温度控制', '终点温度偏低', '焦粉不足或透气性差', '增加焦粉0.3%或提高抽风负压2kPa', 0.88),
    ('生产过程温度控制', '烟道温度梯度异常', '布料偏析导致横向温差', '调整九辊布料器角度, 缩小横向偏差', 0.85),
    ('生产设备操作', '风压异常升高', '料柱透气性恶化或悬料', '减风5-10%, 检查布料矩阵, 必要时坐料处理', 0.90),
    ('生产设备操作', '铁水硅含量偏高', '炉温过高或焦炭负荷偏低', '增加焦炭负荷0.02, 降低风温10°C', 0.87),
    ('生产设备操作', '炉渣碱度波动', '原料成分波动或配料比变化', '调整石灰石/白云石比例, 稳定碱度1.15-1.25', 0.83),
    ('除尘系统', '压差超限', '滤袋堵塞或清灰系统故障', '启动强力清灰, 检查脉冲阀和压缩空气压力', 0.95),
    ('除尘系统', '粉尘排放超标', '滤袋破损或密封不良', '停机检查滤袋, 更换破损滤袋, 检查密封条', 0.93),
    ('除尘系统', '风机能耗偏高', '系统阻力增大或风机效率下降', '优化风门开度, 检查叶轮磨损, 调整变频频率', 0.86),
    ('能源调度', '峰时电价高', '当前处于电力高峰时段(8:00-11:00,18:00-21:00)', '降低可调负荷, 启动储能放电, 优先使用自发电', 0.96),
    ('能源调度', '煤气管网压力波动', '产消不平衡导致压力波动', '调节煤气柜存储量, 调整发电机组负荷, 必要时启动放散', 0.89),
    ('能源调度', '蒸汽管网热损失大', '保温层破损或疏水阀故障', '检查管网保温, 更换故障疏水阀, 优化蒸汽输送压力', 0.84)
ON CONFLICT DO NOTHING;
