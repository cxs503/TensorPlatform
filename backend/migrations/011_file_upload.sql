-- 文件上传模块数据库迁移脚本
-- 创建文件上传管理表

-- 上传文件表
CREATE TABLE IF NOT EXISTS uploaded_files (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,              -- 唯一文件名 (UUID)
    original_filename VARCHAR(255) NOT NULL,     -- 原始文件名
    category VARCHAR(50) DEFAULT 'general',      -- 文件类别
    file_path TEXT NOT NULL,                     -- 文件物理路径
    file_size BIGINT DEFAULT 0,                  -- 文件大小 (字节)
    extension VARCHAR(20),                       -- 文件扩展名
    mime_type VARCHAR(100),                      -- MIME 类型
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 上传时间
    uploaded_by INTEGER,                         -- 上传用户 ID
    description TEXT,                            -- 文件描述
    tags JSONB,                                  -- 标签
    is_public INTEGER DEFAULT 0,                 -- 是否公开
    download_count INTEGER DEFAULT 0,            -- 下载次数
    checksum VARCHAR(64),                        -- 文件校验和
    metadata JSONB,                              -- 元数据
    status VARCHAR(20) DEFAULT 'active',         -- 状态：active, archived, deleted
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_uploaded_files_category ON uploaded_files(category);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_uploaded_at ON uploaded_files(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_uploaded_by ON uploaded_files(uploaded_by);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_status ON uploaded_files(status);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_extension ON uploaded_files(extension);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_original_filename ON uploaded_files(original_filename);

-- 创建类别统计表视图
CREATE OR REPLACE VIEW v_file_category_stats AS
SELECT
    category,
    COUNT(*) as file_count,
    COALESCE(SUM(file_size), 0) as total_size,
    COUNT(DISTINCT uploaded_by) as unique_uploaders
FROM uploaded_files
WHERE status = 'active'
GROUP BY category;

-- 插入默认类别配置
INSERT INTO uploaded_files (filename, original_filename, category, file_path, file_size, extension, description)
SELECT 'placeholder', 'placeholder', cat, '/placeholder', 0, 'txt', '默认类别占位'
FROM (VALUES ('image'), ('document'), ('data'), ('model'), ('archive'), ('code'), ('general')) AS t(cat)
WHERE NOT EXISTS (SELECT 1 FROM uploaded_files WHERE category = cat)
ON CONFLICT DO NOTHING;
