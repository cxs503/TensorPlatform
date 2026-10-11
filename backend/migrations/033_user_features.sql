-- 033: 用户功能权限白名单
-- features: 允许的菜单 path 列表(null/空=不限制; 非空数组=仅允许列出的功能)
-- 配合前端菜单过滤 + 路由守卫; admin 角色恒为不限制。
ALTER TABLE users ADD COLUMN IF NOT EXISTS features JSONB;
COMMENT ON COLUMN users.features IS '允许的功能路径白名单(null/空=不限制)';
