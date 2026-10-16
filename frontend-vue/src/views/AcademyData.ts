// ============================================================
// 开放底座AI学院 — 深度课程数据（AI全栈知识体系）
// 每个模块含：数学基础 + 算法原理 + 代码实战 + 工业落地
// ============================================================

export interface Section {
  title: string
  content: string
  code?: string
  codeLang?: string
  note?: string
  dataRef?: string
  modelRef?: string
  action?: { label: string; action: string; path: string; primary?: boolean; hint?: string; available?: boolean; unavailableHint?: string }
}

export interface LayerSection {
  level: string
  sections: Section[]
}

export interface QuizItem {
  question: string
  options: string[]
  answer: number
  answered?: number
}

export interface Module {
  id: number
  icon: string
  title: string
  desc: string
  done: boolean
  layers: LayerSection[]
  quiz?: QuizItem[]
}

export const academyModules: Module[] = [
  // ═══════════════════════════════════════════
  // 模块1：数据探索与特征工程
  // ═══════════════════════════════════════════
  {
    id: 1, icon: '🔬', title: '数据探索与特征工程', done: false,
    desc: 'EDA方法论→统计基础→数据清洗→特征构造→Pipeline→工业实践',
    layers: [
      { level: 'principle', sections: [
        {
          title: '1.1 探索性数据分析(EDA)完整方法论',
          content: `<b>【科普】什么是EDA？——像医生给病人做检查</b><br><br>数据科学家面对新数据，就像医生面对新病人。在开药方（训练模型）之前，必须先「望闻问切」——看懂数据长什么样、有没有毛病、哪里不对劲。EDA(Exploratory Data Analysis)就是这套系统化的「号脉」方法论，由统计学大师John Tukey在1977年提出。理解EDA是成为合格数据科学家的第一步，也是工业AI项目中最容易被忽视但最关键的一环。<br><br><b>EDA四步法（John Tukey, 1977）：</b><br><br>
<b>第一步：单变量分析</b><br>
• 连续变量：均值(μ)、中位数(Q2)、标准差(σ)、偏度(skewness)、峰度(kurtosis)<br>
• 离散变量：频数分布、众数、熵(H=-Σpᵢlog₂pᵢ)<br>
• 可视化：直方图(看分布形状)、箱线图(看异常值)、小提琴图(看分布密度)<br><br>
<b>第二步：双变量分析</b><br>
• 连续-连续：Pearson相关系数 r=Σ(xᵢ-x̄)(yᵢ-ȳ)/√(Σ(xᵢ-x̄)²Σ(yᵢ-ȳ)²)<br>
  注意：r=0不意味不相关（可能有非线性关系），必须看散点图！<br>
• 连续-离散：分组箱线图、ANOVA F检验<br>
• 离散-离散：卡方检验 χ²=Σ(O-E)²/E<br><br>
<b>第三步：缺失值分析</b><br>
• MCAR(完全随机缺失)：缺失与任何变量无关 → 可直接删除<br>
• MAR(随机缺失)：缺失与观测变量相关 → 可用多重插补<br>
• MNAR(非随机缺失)：缺失与自身值相关 → 最难处理，需业务知识<br><br>
<b>第四步：异常值检测</b><br>
• Z-score法：|z|>3判定异常（假设正态分布）<br>
• IQR法：Q1-1.5×IQR < x < Q3+1.5×IQR<br>
• Isolation Forest：无分布假设，基于树隔离深度`
        },
        {
          title: '1.2 特征工程数学原理',
          content: `<b>标准化 vs 归一化：</b><br>
• Z-score标准化：x'=(x-μ)/σ → 均值0方差1，保留分布形状<br>
• MinMax归一化：x'=(x-xₘᵢₙ)/(xₘₐₓ-xₘᵢₙ) → [0,1]，受异常值影响<br>
• RobustScaler：x'=(x-Q₂)/(Q₃-Q₁) → 基于四分位数，抗异常值<br><br>
<b>编码方法对比：</b><br>
• OneHot：k类→k维稀疏向量。优点：无偏序假设。缺点：维度膨胀<br>
• Label：k类→0~k-1。优点：简单。缺点：引入虚假序关系<br>
• Target Encoding：用目标变量均值替换类别。优点：捕获类别与目标关系。缺点：容易过拟合（需平滑+交叉验证）<br>
• 留一法平滑：encoded=(nⱼ×meanⱼ+N×global_mean)/(nⱼ+N)，N为平滑强度<br><br>
<b>特征选择三大家族：</b><br>
• Filter(过滤法)：方差阈值、卡方检验、互信息I(X;Y)=H(Y)-H(Y|X)<br>
• Wrapper(包装法)：RFE(递归消除)、前向/后向搜索<br>
• Embedded(嵌入法)：L1正则化(Lasso)、树模型特征重要性`,
          code: `import numpy as np
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.feature_selection import mutual_info_regression, SelectKBest

# 异常值检测 — Isolation Forest
from sklearn.ensemble import IsolationForest
iso = IsolationForest(contamination=0.05, random_state=42)
outliers = iso.fit_predict(X)  # -1=异常, 1=正常

# 互信息特征选择（捕获非线性关系）
mi_scores = mutual_info_regression(X, y, random_state=42)
mi_scores = pd.Series(mi_scores, index=X.columns).sort_values(ascending=False)
print("互信息得分:\\n", mi_scores)

# Target Encoding with smoothing
def target_encode_smooth(train, test, col, target, smooth=100):
    global_mean = train[target].mean()
    agg = train.groupby(col)[target].agg(['count','mean'])
    agg['smooth'] = (agg['count']*agg['mean'] + smooth*global_mean) / (agg['count']+smooth)
    return test[col].map(agg['smooth']).fillna(global_mean)`,
          codeLang: 'python'
        },
        {
          title: '1.3 sklearn Pipeline 深度解析',
          content: `<b>Pipeline = 防止数据泄露的工程化方案</b><br><br>
<b>为什么不能手动预处理？</b><br>
如果先对整个数据集做标准化再划分train/test，则测试集的统计信息(z-score的μ和σ)泄露到了训练过程。Pipeline保证fit只用训练集。<br><br>
<b>ColumnTransformer：异构特征处理</b><br>
数值特征→Imputer+Scaler；类别特征→Imputer+OneHotEncoder。一个transformer解决所有列。<br><br>
<b>FeatureUnion：并行特征工程</b><br>
同时运行多个特征提取器(TF-IDF+词嵌入)再拼接。类似神经网络的"多头"思想。<br><br>
<b>内存优化：</b><br>
• 大数据集(>1GB)用partial_fit增量学习<br>
• 用make_pipeline自动命名<br>
• 配合GridSearchCV可直接搜Pipeline参数：param_grid={'pipeline__model__C':[0.1,1,10]}`,
          code: `from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.compose import ColumnTransformer, make_column_selector
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.feature_selection import SelectFromModel
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor

# 全自动：数值列用StandardScaler，类别列用OneHot
preprocessor = ColumnTransformer([
    ('num', Pipeline([
        ('impute', SimpleImputer(strategy='median')),
        ('scale', StandardScaler())
    ]), make_column_selector(dtype_include=np.number)),
    ('cat', Pipeline([
        ('impute', SimpleImputer(strategy='constant', fill_value='missing')),
        ('encode', OneHotEncoder(handle_unknown='ignore', sparse_output=False))
    ]), make_column_selector(dtype_include=object))
], remainder='drop')

# 完整Pipeline：预处理→特征选择→模型
full_pipeline = Pipeline([
    ('preprocess', preprocessor),
    ('feature_select', SelectFromModel(RandomForestRegressor(n_estimators=50), threshold='median')),
    ('model', GradientBoostingRegressor(n_estimators=200, max_depth=6))
])

# GridSearch 直接搜Pipeline参数
from sklearn.model_selection import GridSearchCV
params = {
    'feature_select__threshold': ['mean','median'],
    'model__n_estimators': [100,200],
    'model__learning_rate': [0.05,0.1]
}
grid = GridSearchCV(full_pipeline, params, cv=5, scoring='r2', n_jobs=-1)`,
          codeLang: 'python'
        }
      ]},
      { level: 'scenario', sections: [
        {
          title: '1.4 通用示例：传感器数据全流程分析',
          content: `<b>数据概况：</b>使用包含时间、温度和压力等字段的数值数据集<br><br>
<b>分析步骤：</b><br>
1. 单变量分布检查 → 查看温度、压力等数值列的分布与异常值<br>
2. 缺失率分析 → 逐列统计完整性<br>
3. 相关性矩阵 → 检查冗余特征及其与目标的关系<br>
4. 派生特征 → 根据时间顺序添加滞后值、滑动均值和差分<br><br>
<b>验证要求：</b>在独立验证集上比较派生前后的效果，避免数据泄漏。`,
          dataRef: '用户选择的传感器数据集',
          note: '💡 <b>特征工程：</b>派生特征应具备可解释性，并通过验证集检验其有效性。'
        }
      ]},
      { level: 'practice', sections: [
        {
          title: '1.5 平台实操：完整EDA+特征工程流水线',
          content: `1. 进入 <b>数据集管理</b> → 选择已上传的传感器数据 → 查看数据分布<br>
2. 在 <b>数据集管理</b> 中选择 <b>清洗分析</b> → 查看清洗前后效果与验收结果<br>
3. 进入 <b>特征工程</b> → 选择滞后值、滑动均值等通用转换<br>
4. 打开 <b>Notebook</b> → 运行下方完整分析代码`,
          code: `# 通用传感器数据EDA + 特征工程
import pandas as pd; import numpy as np
import matplotlib.pyplot as plt; import seaborn as sns
from scipy import stats

# 加载数据
# 替换为已上传的数据文件，示例要求包含 temperature 数值列
df = pd.read_csv('/app/uploads/datasets/sensors.csv')
print(f'Shape: {df.shape}\\n{df.describe().round(2)}')

# 1. 缺失率
print('\\n=== 缺失率 ===')
print((df.isnull().sum()/len(df)*100).round(2))

# 2. 异常值检测(IQR)
print('\\n=== 异常值检测(IQR) ===')
for col in df.select_dtypes(np.number).columns:
    Q1,Q3 = df[col].quantile([0.25,0.75]); IQR=Q3-Q1
    n_out = ((df[col] < Q1-1.5*IQR) | (df[col] > Q3+1.5*IQR)).sum()
    if n_out > 0: print(f'  {col}: {n_out} outliers ({n_out/len(df)*100:.1f}%)')

# 3. 偏度和峰度
print('\\n=== 分布形态 ===')
for col in df.select_dtypes(np.number).columns:
    sk = stats.skew(df[col].dropna()); ku = stats.kurtosis(df[col].dropna())
    print(f'  {col}: skew={sk:.3f} kurt={ku:.3f}')

# 4. 相关性矩阵（含派生特征）
df['temperature_lag1'] = df['temperature'].shift(1)
df['temperature_mean5'] = df['temperature'].rolling(5, min_periods=1).mean()
corr = df.select_dtypes(np.number).corr()
plt.figure(figsize=(14,12))
mask = np.triu(np.ones_like(corr, dtype=bool))
sns.heatmap(corr, mask=mask, annot=True, fmt='.2f', cmap='RdBu_r', center=0)
plt.title('传感器参数相关性矩阵（含派生特征）'); plt.tight_layout(); plt.show()

# 5. 目标变量与特征关系
fig, axes = plt.subplots(2,4, figsize=(16,8))
for ax, col in zip(axes.flat, df.select_dtypes(np.number).drop(columns=['temperature']).columns):
    ax.scatter(df[col], df['temperature'], alpha=0.1, s=1)
    ax.set_xlabel(col, fontsize=9); ax.set_ylabel('temperature', fontsize=9)
plt.suptitle('各特征 vs temperature'); plt.tight_layout(); plt.show()`,
          codeLang: 'python',
          action: { label: '📓 打开 Notebook', action: 'nav', path: '/model/notebook', primary: true }
        }
      ]}
    ],
    quiz: [
      { question: '标准化和归一化的区别是什么？', options: ['完全相同', '标准化用(μ,σ)，归一化用(min,max)', '标准化更快', '归一化=标准化'], answer: 1 },
      { question: 'MCAR/MAR/MNAR哪种最难处理？', options: ['MCAR', 'MAR', 'MNAR', '都一样'], answer: 2 },
    ]
  },

  // ═══════════════════════════════════════════
  // 模块2：回归预测模型训练
  // ═══════════════════════════════════════════
  {
    id: 2, icon: '📈', title: '回归预测与经典ML', done: false,
    desc: '线性回归数学→决策树分裂→RF/XGB原理→偏差方差→超参调优',
    layers: [
      { level: 'principle', sections: [
        {
          title: '2.1 线性回归的数学本质',
          content: `<b>【科普】机器学习到底在「学」什么？</b><br><br>一句话概括：机器学习 = 从数据中自动发现规律。你给模型一堆「问题+答案」的训练数据，模型自己找出从问题到答案的映射规则。这不是魔法——背后是数学优化：定义一个损失函数(衡量预测好坏)，用梯度下降不断调整参数，让损失越来越小。从最简单的线性回归到工业级XGBoost，核心思想一脉相承——理解经典，方能驾驭前沿。<br><br><b>OLS(普通最小二乘)的矩阵推导：</b><br>
• 模型：y=Xβ+ε，X∈ℝⁿˣᵖ<br>
• 损失函数(SSE)：L(β)=||y-Xβ||²=(y-Xβ)ᵀ(y-Xβ)<br>
• 对β求导=0：∂L/∂β=-2Xᵀ(y-Xβ)=0 → <b>β̂=(XᵀX)⁻¹Xᵀy</b>（正规方程）<br><br>
<b>正规方程的问题：</b>XᵀX需要可逆。当特征高度相关(共线性)→XᵀX近似奇异→逆不稳定→系数方差极大。<br>
<b>Ridge回归(L2)：</b>β̂=(XᵀX+λI)⁻¹Xᵀy → 对角加载λ使矩阵可逆，惩罚大系数<br>
<b>Lasso回归(L1)：</b>min||y-Xβ||²+λΣ|βⱼ| → L1范数产生稀疏解（自动特征选择）<br>
<b>ElasticNet：</b>α·L1+(1-α)·L2 → 兼具稀疏性和稳定性<br><br>
<b>R²的陷阱：</b>R²=1-SSE/TSS。加一个随机噪声特征，R²上升→调整R²：adj_R²=1-(1-R²)(n-1)/(n-p-1)`,
          code: `import numpy as np
# 正规方程手工实现
class LinearRegressionScratch:
    def fit(self, X, y):
        X = np.c_[np.ones(X.shape[0]), X]  # 加截距列
        self.coef_ = np.linalg.inv(X.T @ X) @ X.T @ y
        return self
    def predict(self, X):
        X = np.c_[np.ones(X.shape[0]), X]
        return X @ self.coef_

# Ridge正则化路径可视化
from sklearn.linear_model import Ridge
alphas = np.logspace(-3, 5, 100)
coefs = []
for a in alphas:
    ridge = Ridge(alpha=a).fit(X_train, y_train)
    coefs.append(ridge.coef_)
# plot alphas vs coefs → 观察系数收缩过程`,
          codeLang: 'python'
        },
        {
          title: '2.2 决策树与集成学习的深层原理',
          content: `<b>CART分类树的分裂准则：</b><br>
• 分类：Gini(D)=1-Σpₖ² 或 熵 H(D)=-Σpₖlog₂pₖ<br>
• 回归：MSE=(1/|D|)Σ(yᵢ-ȳ)² 中前后减少量最大<br>
• 分裂收益：Δ=I(parent)-Σ(|Dⱼ|/|D|)·I(Dⱼ)<br><br>
<b>剪枝（防止过拟合）：</b><br>
• 预剪枝：max_depth/min_samples_split/min_samples_leaf/min_impurity_decrease<br>
• 后剪枝：先建完整树→自底向上用验证集评估剪枝→选最优子树<br>
• 代价复杂度剪枝：Rα(T)=R(T)+α·|T|，α控制复杂度惩罚<br><br>
<b>Bagging(随机森林)的统计学解释：</b><br>
Var(平均值)=σ²/n·(1+(n-1)ρ) → 相关性ρ越低的树，集成效果越好。随机森林通过Bootstrap采样(ρ₁)和随机子特征(ρ₂)同时降低树间相关性。<br><br>
<b>Boosting(XGBoost)的数学：</b><br>
• 加法模型：Fₘ(x)=Fₘ₋₁(x)+η·hₘ(x)，η为学习率<br>
• 损失函数泰勒展开：L(y,Fₘ₋₁+h)≈L(y,Fₘ₋₁)+g·h+½h²·Hessian<br>
• 最优叶节点权重：wⱼ*=-Σgᵢ/(ΣHᵢ+λ)<br>
• XGBoost特有：二阶泰勒展开(牛顿法增强→更快收敛)、列采样(类似RF)、Shrinkage(η<1)`,
          code: `# 对比Bagging vs Boosting在不同噪声水平下的表现
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor
import numpy as np

# 生成数据
X = np.random.randn(1000, 5)
y = 3*X[:,0] + 2*X[:,1]**2 + np.random.randn(1000)*0.5

for noise_level in [0.1, 0.5, 1.0]:
    y_noisy = y + np.random.randn(1000)*noise_level
    rf = RandomForestRegressor(n_estimators=100).fit(X, y_noisy)
    xgb = XGBRegressor(n_estimators=100, learning_rate=0.1).fit(X, y_noisy)
    # RF对噪声更鲁棒(Bagging降方差)，XGB在低噪声下精度更高(Boosting降偏差)`,
          codeLang: 'python'
        },
        {
          title: '2.3 偏差-方差分解：ML最核心的概念',
          content: `<b>完整推导：</b><br>
E[(y-f̂(x))²] = (Bias[f̂(x)])² + Var[f̂(x)] + σ²<br>
其中 Bias[f̂(x)]=E[f̂(x)]-f(x)，Var[f̂(x)]=E[(f̂(x)-E[f̂(x)])²]，σ²为不可约噪声<br><br>
<b>模型复杂度与误差的关系（U型曲线）：</b><br>
• 低复杂度(欠拟合)：高偏差，低方差 → 训练/测试误差都高<br>
• 最优复杂度：偏差≈方差 → 总误差最小<br>
• 高复杂度(过拟合)：低偏差，高方差 → 训练误差低/测试误差高<br><br>
<b>实战诊断工具：</b><br>
1. 学习曲线：画训练集和验证集误差随样本量变化的曲线 → gap大小=方差大小<br>
2. 验证曲线：画训练/验证误差随模型复杂度(如max_depth)变化的曲线 → 找U型底<br>
3. 集成效果：用OOB误差代替验证集 → RF天然支持<br><br>
<b>工业数据特别考虑：</b><br>
• 时序数据不能用随机CV → 必须用TimeSeriesSplit<br>
• 分组数据(同一设备多次采样) → 必须用GroupKFold，防止数据泄露`,
          code: `from sklearn.model_selection import learning_curve
import matplotlib.pyplot as plt

train_sizes, train_scores, test_scores = learning_curve(
    RandomForestRegressor(n_estimators=100, max_depth=15),
    X, y, cv=5, scoring='r2', n_jobs=-1,
    train_sizes=np.linspace(0.1, 1.0, 10)
)

train_mean = train_scores.mean(axis=1)
test_mean = test_scores.mean(axis=1)
gap = train_mean - test_mean  # gap大=过拟合

plt.plot(train_sizes, train_mean, 'o-', label='Train R²')
plt.plot(train_sizes, test_mean, 'o-', label='Test R²')
plt.fill_between(train_sizes, train_mean, test_mean, alpha=0.2, label=f'Gap(gap.mean():.3f)')
plt.xlabel('Training samples'); plt.ylabel('R² Score')
plt.legend(); plt.title('Learning Curve: 诊断欠拟合/过拟合')`,
          codeLang: 'python'
        }
      ]},
      { level: 'scenario', sections: [
      {
        title: '2.4 综合案例：多场景回归对比',
        content: `<b>案例1：设备温度预测(RF, R²=0.92)</b><br>
• 数据量：43,884行 → 大样本，RF/XGBoost优势明显<br>
• 特征重要性分析：温度传感器(31%)>压力传感器(22%)>流量计(18%)<br>
• 模型选择理由：参数间非线性交互强(高负载×低流量→温度骤降)，树模型天然捕获<br><br>
<b>案例2：交通流量预测(LightGBM, R²=0.89)</b><br>
• 数据量：52,560行(15分钟/点，1年) → 强季节性<br>
• 关键特征：lag24(昨天同时刻)、lag168(上周同时刻)、是否节假日、天气编码<br>
• 陷阱：不能用随机CV，必须用TimeSeriesSplit！`,
          modelRef: '设备温度预测-RF-v2 | R²=0.92 | 交通流量预测-LGBM | R²=0.89'
        }
      ]},
      { level: 'practice', sections: [
        {
          title: '2.5 平台实操 + 超参优化完整流程',
          content: `1. <b>模型训练</b> → 选数据→RF/XGB→训练→看特征重要性<br>
2. <b>超参搜索</b> → Optuna贝叶斯优化→自动找最优参数<br>
3. <b>Notebook</b> → 运行完整对比实验代码`,
          code: `# 完整模型对比 + Optuna超参优化
import optuna
from sklearn.model_selection import cross_val_score
from xgboost import XGBRegressor

def objective(trial):
    params = {
        'n_estimators': trial.suggest_int('n_estimators', 50, 300),
        'max_depth': trial.suggest_int('max_depth', 3, 15),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
        'subsample': trial.suggest_float('subsample', 0.6, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
        'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 10, log=True),
        'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 10, log=True),
    }
    model = XGBRegressor(**params, random_state=42)
    scores = cross_val_score(model, X_train, y_train, cv=5, scoring='r2')
    return scores.mean()

study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=50, show_progress_bar=True)

print(f"Best R²: {study.best_value:.3f}")
print(f"Best params: {study.best_params}")
# 在测试集上评估最佳模型
best_model = XGBRegressor(**study.best_params).fit(X_train, y_train)
test_r2 = best_model.score(X_test, y_test)
print(f"Test R²: {test_r2:.3f}")`,
          codeLang: 'python',
          action: { label: '🚀 打开模型训练', action: 'nav', path: '/model/training-tasks', primary: true }
        }
      ]}
    ],
    quiz: [
      { question: 'Ridge vs Lasso回归的核心区别？', options: ['完全相同', 'L1(稀疏)vs L2(收缩)', '速度不同', 'Lasso只能分类'], answer: 1 },
      { question: 'Bagging降低什么？Boosting降低什么？', options: ['都是方差', 'Bagging降方差,Boosting降偏差', '都是偏差', 'Bagging降偏差,Boosting降方差'], answer: 1 },
    ]
  },

  // ═══════════════════════════════════════════
  // 模块3：深度学习与神经网络
  // ═══════════════════════════════════════════
  {
    id: 3, icon: '🧠', title: '深度学习与神经网络', done: false,
    desc: '反向传播→CNN架构→Transformer注意力→迁移学习→PyTorch实战',
    layers: [
      { level: 'principle', sections: [
        {
          title: '3.1 反向传播与自动微分',
          content: `<b>【科普】神经网络是什么？——模仿人脑的计算模型</b><br><br>人类大脑有约860亿个神经元，每个接收信号→决定是否激活→传递信号。人工神经网络就是对这一过程的数学抽象。虽然每个神经元只是简单的「加权求和+非线性激活」，但当成千上万个神经元分层堆叠时，就能涌现出惊人的智能——从识别猫的图片到写出媲美人类的文章。深度学习的「深度」，就是指网络的层数。越深，能学习的模式越复杂。<br><br><b>神经网络 = 复合函数：</b>ŷ=fₙ(fₙ₋₁(...f₁(x)))，每层fᵢ=σ(Wᵢx+bᵢ)<br><br>
<b>链式法则(反向传播核心)：</b><br>
∂L/∂Wₖ = ∂L/∂aₙ · ∂aₙ/∂aₙ₋₁ · ... · ∂aₖ₊₁/∂aₖ · ∂aₖ/∂Wₖ<br>
每一步只需要前一层的梯度(局部计算)→内存高效<br><br>
<b>激活函数的梯度特性：</b><br>
• Sigmoid: σ'(x)=σ(x)(1-σ(x)) → 饱和区梯度≈0(梯度消失)<br>
• Tanh: tanh'(x)=1-tanh²(x) → 零中心(比sigmoid好)但仍有饱和问题<br>
• ReLU: f'(x)=1 if x>0 else 0 → 不饱和但"死神经元"问题(x<0时梯度=0)<br>
• LeakyReLU: f'(x)=1 if x>0 else 0.01 → 解决死神经元<br>
• GELU: Φ(x)·x 其中Φ是标准正态CDF → Transformer标配<br><br>
<b>优化器演进：</b><br>
• SGD: W=W-η·∇L → 朴素，容易震荡<br>
• SGD+Momentum: v=βv+η∇L; W=W-v → 惯性加速，越过局部最优<br>
• Adam: m=β₁m+(1-β₁)∇L, v=β₂v+(1-β₂)∇L² → 自适应学习率，工业首选<br>
• AdamW: 解耦weight decay → Adam+正确L2正则化`,
          code: `import torch
import torch.nn as nn

# 手工实现两层MLP + 反向传播（教学版）
class MLPFromScratch:
    def __init__(self, in_dim, hidden, out_dim, lr=0.01):
        self.W1 = torch.randn(in_dim, hidden) * 0.01
        self.b1 = torch.zeros(hidden)
        self.W2 = torch.randn(hidden, out_dim) * 0.01
        self.b2 = torch.zeros(out_dim)
        self.lr = lr
    
    def forward(self, X):
        self.z1 = X @ self.W1 + self.b1
        self.a1 = torch.relu(self.z1)
        self.z2 = self.a1 @ self.W2 + self.b2
        return self.z2
    
    def backward(self, X, y, pred):
        m = X.shape[0]
        dz2 = (pred - y) / m
        self.dW2 = self.a1.T @ dz2
        self.db2 = dz2.sum(0)
        da1 = dz2 @ self.W2.T
        dz1 = da1 * (self.z1 > 0).float()  # ReLU导数
        self.dW1 = X.T @ dz1
        self.db1 = dz1.sum(0)
        # 更新
        self.W2 -= self.lr * self.dW2; self.b2 -= self.lr * self.db2
        self.W1 -= self.lr * self.dW1; self.b1 -= self.lr * self.db1`,
          codeLang: 'python'
        },
        {
          title: '3.2 CNN架构深度解析',
          content: `<b>卷积运算 = 互相关：</b>Output[i,j]=ΣₘΣₙ Input[i+m,j+n]·Kernel[m,n]<br><br>
<b>为什么卷积有效？（三大归纳偏置）</b><br>
1. <b>平移等变性：</b>平移输入→平移输出。适合图像(猫在左上角/右下角都是猫)<br>
2. <b>局部性：</b>每个神经元只看局部区域(3×3/5×5)→参数少<br>
3. <b>层次化特征：</b>浅层=边缘/纹理；中层=形状/部件；深层=语义/对象<br><br>
<b>经典架构设计哲学：</b><br>
• <b>VGG(2014)：</b>只用3×3卷积堆叠。两个3×3≈一个5×5(感受野同但参数少)。证明"深度"比"大核"重要<br>
• <b>ResNet(2015)残差连接：</b>H(x)=F(x)+x。如果最优是恒等映射→让F(x)→0比学恒等更容易！解决了152层梯度消失问题<br>
• <b>MobileNet(2017)深度可分离卷积：</b>Depthwise(各通道独立3×3)+Pointwise(1×1跨通道)=参数量减少8-9倍，移动端推理<br>
• <b>EfficientNet(2019)：</b>NAS搜索最优(depth, width, resolution)比例 → d=α^φ, w=β^φ, r=γ^φ 其中α·β²·γ²≈2`,
          code: `# ResNet残差块实现
class ResidualBlock(nn.Module):
    def __init__(self, in_ch, out_ch, stride=1):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, stride, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_ch)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(out_ch)
        # 捷径连接：如果维度变化用1x1卷积对齐
        self.shortcut = nn.Sequential()
        if stride != 1 or in_ch != out_ch:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 1, stride, bias=False),
                nn.BatchNorm2d(out_ch))
    
    def forward(self, x):
        out = torch.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)  # F(x) + x
        return torch.relu(out)`,
          codeLang: 'python'
        },
        {
          title: '3.3 Transformer：Attention机制的数学',
          content: `<b>自注意力公式（逐步推导）：</b><br>
输入X∈ℝⁿˣᵈ → 线性投影 → Q=XWq, K=XWk, V=XWv (Wq,Wk,Wv∈ℝᵈˣᵈₖ)<br>
Attention(Q,K,V) = softmax(QKᵀ/√dₖ) V<br><br>
<b>为什么除以√dₖ？</b><br>
假设q,k各分量独立~N(0,1)，则 q·k = Σqᵢkᵢ ~ N(0,dₖ)<br>
→ 方差=dₖ → 点积值可能很大 → softmax饱和→梯度接近0<br>
→ 除以√dₖ → 方差=1 → softmax在合理范围 → 梯度正常<br><br>
<b>多头注意力 = 多角度关注：</b><br>
MultiHead(Q,K,V)=Concat(head₁,...,headₕ)Wₒ<br>
headᵢ=Attention(QWqᵢ,KWkᵢ,VWvᵢ)<br>
每个头学习不同的注意力模式（语法/语义/位置）<br><br>
<b>位置编码：</b><br>
• 正弦编码：PE(pos,2i)=sin(pos/10000^(2i/d)), PE(pos,2i+1)=cos(pos/10000^(2i/d))<br>
• 可学习位置编码(BERT/GPT)：让模型自己学位置关系 → 更灵活但外推受限`,
          code: `import torch.nn.functional as F

class MultiHeadAttention(nn.Module):
    def __init__(self, d_model=512, n_heads=8):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_k = d_model // n_heads
        self.n_heads = n_heads
        self.Wq = nn.Linear(d_model, d_model)
        self.Wk = nn.Linear(d_model, d_model)
        self.Wv = nn.Linear(d_model, d_model)
        self.Wo = nn.Linear(d_model, d_model)
    
    def forward(self, x, mask=None):
        B, T, D = x.shape
        Q = self.Wq(x).view(B, T, self.n_heads, self.d_k).transpose(1,2)
        K = self.Wk(x).view(B, T, self.n_heads, self.d_k).transpose(1,2)
        V = self.Wv(x).view(B, T, self.n_heads, self.d_k).transpose(1,2)
        
        scores = (Q @ K.transpose(-2,-1)) / (self.d_k ** 0.5)
        if mask is not None:
            scores = scores.masked_fill(mask==0, float('-inf'))
        attn = F.softmax(scores, dim=-1)
        out = (attn @ V).transpose(1,2).contiguous().view(B,T,D)
        return self.Wo(out)`,
          codeLang: 'python'
        }
      ]},
      { level: 'scenario', sections: [
        {
          title: '3.4 工业深度学习应用场景',
          content: `<b>CV(计算机视觉)：</b><br>
• 产品缺陷检测 → ResNet迁移学习(5类缺陷/数百样本即可)<br>
• 安全帽/工服检测 → YOLO目标检测<br>
• 仪表读数 → OCR+分类<br><br>
<b>NLP(自然语言处理)：</b><br>
• 设备故障报告分类 → BERT微调<br>
• 工单自动路由 → TextCNN/BiLSTM<br>
• 操作规范问答 → RAG+大模型<br><br>
<b>时序异常检测(深度学习)：</b><br>
• LSTM-AutoEncoder: 学习正常模式→重构误差大=异常<br>
• TFT(Temporal Fusion Transformer): 可解释时序预测`,
          modelRef: '平台CV模型: ResNet18/MobileNetV2 | NLP: TF-IDF/BERT | 时序: LSTM/Prophet'
        }
      ]},
      { level: 'practice', sections: [
        {
          title: '3.5 PyTorch训练完整循环',
          content: `标准训练循环 + 验证 + 早停 + 学习率调度：`,
          code: `def train_epoch(model, loader, optimizer, criterion, device):
    model.train()
    total_loss, correct, total = 0, 0, 0
    for X, y in loader:
        X, y = X.to(device), y.to(device)
        optimizer.zero_grad()
        output = model(X)
        loss = criterion(output, y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        _, pred = output.max(1)
        correct += pred.eq(y).sum().item()
        total += y.size(0)
    return total_loss/len(loader), correct/total

# 早停机制
class EarlyStopping:
    def __init__(self, patience=10, min_delta=0.001):
        self.patience = patience; self.min_delta = min_delta
        self.counter = 0; self.best_score = None
    
    def __call__(self, val_score):
        if self.best_score is None:
            self.best_score = val_score
        elif val_score < self.best_score + self.min_delta:
            self.counter += 1
            if self.counter >= self.patience: return True
        else:
            self.best_score = val_score; self.counter = 0
        return False

# 完整训练
model = ResNet18(num_classes=5).to(device)
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0.01)
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=50)
criterion = nn.CrossEntropyLoss()
stopper = EarlyStopping(patience=10)

for epoch in range(100):
    train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, device)
    val_loss, val_acc = validate(model, val_loader, criterion, device)
    scheduler.step()
    print(f'Epoch {epoch}: train_loss={train_loss:.3f}, val_acc={val_acc:.3f}')
    if stopper(-val_acc):  # 最大化val_acc→取负
        print(f'Early stopping at epoch {epoch}')
        break`,
          codeLang: 'python',
          action: { label: '🖼️ CV训练（暂不可用）', action: 'nav', path: '/model/cv-training', primary: true, available: false, unavailableHint: 'CV训练当前处于不可用状态；本节可继续学习原理，实操请使用已开放的算法商店或代码编辑器。' }
        }
      ]}
    ],
    quiz: [
      { question: '为什么Transformer的Attention要除以√dₖ？', options: ['为了更快', '防止softmax饱和(方差控制)', '这是bug', '增加模型容量'], answer: 1 },
      { question: 'ResNet的残差连接为什么有效？', options: ['增加参数', '让学习恒等映射更容易', '单纯堆深度', '减少计算量'], answer: 1 },
    ]
  },

// ═══════════════════════════════════════════
// 模块4：模型评估与实验管理
// ═══════════════════════════════════════════
{
  id: 4, icon: '🧪', title: '模型评估与实验管理', done: false,
  desc: '交叉验证→评估指标→MLflow→Optuna→实验对比→A/B测试',
  layers: [
    { level: 'principle', sections: [
      {
        title: '4.1 交叉验证的统计学基础',
        content: `<b>【科普】「准确率99%」就是好模型吗？——并不一定！</b><br><br>假如1000个工业设备中只有1个故障(0.1%)，一个模型对所有设备都说「正常」——准确率高达99.9%！但这个模型完全没用，因为它一个故障都检测不出来。这就是为什么评估模型需要多维度指标：混淆矩阵看漏报/误报，ROC-AUC看排序能力，F1-score平衡精确率和召回率。评估就像一面「照妖镜」，让你看清模型的真实面目。<br><br><b>为什么需要交叉验证？</b><br>
单次train/test划分的评估方差太大（数据集不同→结果可能差10%以上）。<br><br>
<b>K-Fold CV的偏差-方差分析：</b><br>
• K=2：低方差但高偏差（训练数据太少）<br>
• K=10：低偏差但高方差（各折差异大）<br>
• K=5或10：业界平衡点<br>
• Leave-One-Out(K=n)：偏差≈0但方差极大+计算昂贵<br><br>
<b>分层K-Fold(StratifiedKFold)：</b>保持每折的类别比例与整体一致。分类任务默认使用！<br>
<b>时序交叉验证(TimeSeriesSplit)：</b>第K折的训练集是前K个时间段，测试集是第K+1个。工业时序预测必须使用！<br>
<b>分组交叉验证(GroupKFold)：</b>同一设备/病人的数据不能跨折→防止过拟合信号泄露。`,
        code: `from sklearn.model_selection import (KFold, StratifiedKFold, TimeSeriesSplit, GroupKFold, cross_val_score)
from xgboost import XGBRegressor

model = XGBRegressor()
# 标准5折CV
scores = cross_val_score(model, X, y, cv=5, scoring='r2')
print(f'5-Fold CV R²: {scores.mean():.3f} ± {scores.std():.3f}')

# 时序CV（必须！）
tscv = TimeSeriesSplit(n_splits=5)
for i, (train_idx, test_idx) in enumerate(tscv.split(X)):
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    # 验证：test_idx 的所有时间戳 > train_idx 的所有时间戳`,
        codeLang: 'python'
      },
      {
        title: '4.2 回归与分类指标完整手册',
        content: `<b>回归指标选择指南：</b><br>
• R²：解释方差占比[-∞,1]。对异常值敏感(平方放大)。>0.9很好，>0.7可用<br>
• MAE：平均绝对误差。鲁棒，可解释（单位与目标相同）<br>
• RMSE：对大误差惩罚重(平方)。工业场景如果大误差代价高→用RMSE<br>
• MAPE：百分比误差。跨量纲比较。不能用于y=0的数据<br>
• SMAPE：对称MAPE=2|y-ŷ|/(|y|+|ŷ|)。解决了MAPE不对称问题<br><br>
<b>分类指标与使用场景：</b><br>
• Accuracy：样本均衡时用<br>
• Precision：误报代价高（如误判设备故障→不必要停机）<br>
• Recall：漏报代价高（如漏判缺陷→不合格品流出）<br>
• F1：均衡Precision和Recall。支持micro/macro/weighted平均<br>
• AUC-ROC：不依赖阈值，衡量排序能力。>0.9优秀，>0.8良好<br>
• LogLoss：概率校准质量。Kaggle常用`,
        code: `from sklearn.metrics import *
import numpy as np

def full_evaluation(y_true, y_pred, y_proba=None, task='regression'):
    results = {}
    if task == 'regression':
        results['R²'] = r2_score(y_true, y_pred)
        results['MAE'] = mean_absolute_error(y_true, y_pred)
        results['RMSE'] = np.sqrt(mean_squared_error(y_true, y_pred))
        results['MAPE'] = np.mean(np.abs((y_true-y_pred)/(y_true+1e-8)))*100
        results['ExplainedVar'] = explained_variance_score(y_true, y_pred)
    else:
        results['Accuracy'] = accuracy_score(y_true, y_pred)
        results['Precision'] = precision_score(y_true, y_pred, average='weighted', zero_division=0)
        results['Recall'] = recall_score(y_true, y_pred, average='weighted', zero_division=0)
        results['F1'] = f1_score(y_true, y_pred, average='weighted', zero_division=0)
        if y_proba is not None:
            results['AUC'] = roc_auc_score(y_true, y_proba, multi_class='ovr', average='weighted')
    return results`,
        codeLang: 'python'
      },
    ]},
    { level: 'scenario', sections: [
      {
        title: '4.3 MLflow实验管理最佳实践',
        content: `<b>MLflow Tracking核心概念：</b><br>
• Run：一次训练运行。记录参数(param)、指标(metric)、模型文件(artifact)<br>
• Experiment：一组相关的Runs。可嵌套命名空间<br>
• Registry：模型版本管理。Staging→Production→Archived<br><br>
<b>平台MLflow集成：</b>每次训练自动记录超参数和指标。实验对比页面可选中多个Run并排对比（ECharts柱状图）。<br><br>
<b>Optuna + MLflow 联合使用：</b>每个Trial自动创建MLflow Run→所有超参搜索结果保留在MLflow中。`,
        modelRef: 'MLflow Tracking Server → 平台实验对比页面'
      }
    ]},
    { level: 'practice', sections: [
      {
        title: '4.4 平台实验对比实操',
        content: `1. <b>模型训练</b> → 训练多个模型（每个自动记录MLflow）<br>
2. <b>实验对比追踪</b> → 勾选实验→并排查看指标柱状图<br>
3. <b>评估中心</b> → 新建评估任务→选择模型和数据集`,
        action: { label: '🔬 打开实验对比', action: 'nav', path: '/model/experiments', primary: true }
      }
    ]}
  ],
  quiz: [
    { question: '工业时序预测必须用哪种CV？', options: ['KFold', 'StratifiedKFold', 'TimeSeriesSplit', 'LOO'], answer: 2 },
    { question: '误报代价高的场景应关注哪个指标？', options: ['Recall', 'Precision', 'F1', 'Accuracy'], answer: 1 },
  ]
},

// ═══════════════════════════════════════════
// 模块5：模型解释与可解释AI
// ═══════════════════════════════════════════
{
  id: 5, icon: '🔍', title: '模型解释与可解释AI', done: false,
  desc: 'SHAP博弈论→LIME局部逼近→PDP边际效应→ICE个体→工业合规',
  layers: [
    { level: 'principle', sections: [
      {
        title: '5.1 SHAP：Shapley值的机器学习应用',
        content: `<b>【科普】AI说「设备明天会坏」——你敢信吗？</b><br><br>在工业场景中，盲目相信AI的预测可能造成巨大损失。如果一个模型告诉你「这个设备明天会故障」，你必须知道：<b>为什么？</b>是哪个传感器的什么异常数据导致了这个判断？可解释AI(XAI)就是回答这些问题的技术——它让「黑盒」模型变得透明。SHAP方法基于2012年诺贝尔经济学奖的Shapley值理论，能公平地量化每个特征对预测结果的贡献。<br><br><b>Shapley值的博弈论起源（Lloyd Shapley, 1953）：</b><br>
N个玩家合作获得总收益v(N)。玩家i的Shapley值=其对所有可能联盟的边际贡献的加权平均：<br>
φᵢ(v) = Σ_{S⊆N\\{i}} [|S|!·(n-|S|-1)!/n!] · [v(S∪{i})-v(S)]<br><br>
<b>SHAP如何映射到ML：</b><br>
• 玩家=特征<br>
• 总收益=模型预测值-平均预测值<br>
• 边际贡献=加入该特征前后的预测变化<br>
• 穷举所有特征子集→计算加权平均→得到每个特征的SHAP值<br><br>
<b>SHAP三大特性（唯一满足的方法）：</b><br>
1. <b>局部准确性：</b>f(x)=φ₀+Σφⱼ — 预测值=基准值+所有SHAP值之和<br>
2. <b>缺失性：</b>缺失特征贡献为0<br>
3. <b>一致性：</b>如果模型改变使得特征j贡献增大，则φⱼ不会减小<br><br>
<b>TreeSHAP（树模型加速）：</b>利用树结构在O(TLD²)时间内精确计算（vs 暴力法O(2ⁿ)）`,
        code: `import shap
import matplotlib.pyplot as plt

# TreeSHAP（精确+快速）
explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X_test)

# 1. 全局重要性（Summary Plot）
shap.summary_plot(shap_values, X_test, feature_names=features, show=False)
plt.title('SHAP Feature Importance (Summary Plot)')

# 2. 单个预测的瀑布图
shap.waterfall_plot(shap.Explanation(
    values=shap_values[0],
    base_values=explainer.expected_value,
    data=X_test.iloc[0].values,
    feature_names=features
))

# 3. 特征交互（依赖图）
shap.dependence_plot('烟道温度', shap_values, X_test,
                     interaction_index='机速', feature_names=features)

# 4. 全局条形图
shap.plots.bar(shap.Explanation(shap_values, feature_names=features))`,
        codeLang: 'python'
      },
      {
        title: '5.2 LIME与PDP：可解释性工具链',
        content: `<b>LIME(Local Interpretable Model-agnostic Explanations)：</b><br>
在预测点附近采样扰动数据→训练简单可解释模型(线性/决策树)→用该模型解释原预测。<br>
• 优点：模型无关（任何黑箱都可用）<br>
• 缺点：局部近似可能不准确；采样方式影响结果<br><br>
<b>PDP(部分依赖图)：</b><br>
PDPⱼ(x)=E[f(Xⱼ=x, X_{-j})] ≈ (1/n)Σᵢf(x, xᵢ_{-j})<br>
固定特征j为值x，其他特征保持原值→对所有样本平均。展示特征j的边际效应。<br>
• 优点：全局视角，直观<br>
• 缺点：假设特征独立（现实中不成立）<br><br>
<b>ICE(个体条件期望)：</b>PDP的去平均版 — 画出每个样本的曲线而非平均。<br>
• 发现异质性：某些样本的特征效应可能与平均方向相反`,
        code: `from sklearn.inspection import PartialDependenceDisplay

# PDP + ICE 联合图
fig, ax = plt.subplots(figsize=(10,6))
PartialDependenceDisplay.from_estimator(
    model, X_train, ['烟道温度','机速','料层透气率'],
    kind='both',  # 'both'=PDP+ICE, 'average'=仅PDP, 'individual'=仅ICE
    grid_resolution=50, ax=ax
)
plt.suptitle('Partial Dependence + ICE Plots')`,
        codeLang: 'python'
      },
    ]},
    { level: 'scenario', sections: [
      {
        title: '5.3 工业案例：生产过程温度预测的SHAP分析',
        content: `<b>SHAP分析关键发现：</b><br>
• <b>烟道温度</b>：SHAP值范围[-12,+15]°C → 最关键因素。高烟道温度→终点温度升高15°C<br>
• <b>料层透气率</b>：SHAP值范围[-10,+8]°C → 透气性越好→温度越低（散热快），符合生产过程工艺物理规律<br>
• <b>交互效应</b>：高机速×低料层厚度→终点温度骤降（SHAP interaction value高）→ 操作建议：加速时必须同步调高料层<br><br>
<b>工艺验证：</b>SHAP结论与生产过程工艺手册一致→模型学到的是物理规律，不是数据噪声。`,
        modelRef: '生产过程终点温度预测-RF-v2 + SHAP分析',
        note: '🔬 <b>可解释AI的工业价值：</b>不仅告诉你"预测温度会下降"，还告诉你"因为透气率从0.8升到1.2导致-8°C"→操作员可以做针对性调整。'
      }
    ]},
    { level: 'practice', sections: [
      {
        title: '5.4 平台实操',
        content: `1. <b>模型解释</b> → 选择已训练模型<br>
2. 查看SHAP Summary Plot和特征重要性排名<br>
3. 用PDP理解关键特征的边际效应<br>
4. 导出SHAP报告给业务团队`,
        action: { label: '🔍 打开模型解释', action: 'nav', path: '/model/model-explain', primary: true }
      }
    ]}
  ],
  quiz: [
    { question: 'SHAP值的理论基础来自？', options: ['信息论', '博弈论(Shapley值)', '概率论', '控制论'], answer: 1 },
    { question: 'PDP假设什么（主要局限）？', options: ['线性关系', '特征独立', '正态分布', '大样本'], answer: 1 },
  ]
},

// ═══════════════════════════════════════════
// 模块6：MLOps与自动化部署
// ═══════════════════════════════════════════
{
  id: 6, icon: '🚀', title: 'MLOps与自动化部署', done: false,
  desc: 'CI/CD/CT→Docker容器化→ONNX加速→推理服务→监控漂移→版本治理',
  layers: [
    { level: 'principle', sections: [
      {
        title: '6.1 MLOps三层自动化',
        content: `<b>【科普】为什么Notebook里跑得很好的模型，一上线就翻车？</b><br><br>很多人以为AI项目最难的环节是训练模型——其实恰恰相反。真正的挑战在于让模型<b>可靠地运行在生产环境</b>：数据分布变了怎么办？依赖冲突怎么解决？模型性能下降了谁负责？MLOps(机器学习运维)应运而生——把DevOps的理念引入ML，覆盖从训练到退役的全生命周期。不做MLOps，AI永远停留在「实验室阶段」。<br><br><b>MLOps = DevOps + Data + Model 的三位一体：</b><br><br>
<b>CI(持续集成)：</b>代码push→自动测试→构建镜像（不只是代码，还包括数据验证+模型验证）<br>
<b>CD(持续交付)：</b>自动化部署流水线：模型Registry→打包→部署→健康检查<br>
<b>CT(持续训练)：</b>监控触发自动重训练：数据漂移检测→触发Retrain Pipeline→评估→自动上线(需人工审批)<br><br>
<b>ML特有的挑战（vs传统DevOps）：</b><br>
1. 数据依赖 → 数据版本化(DVC)<br>
2. 模型退化 → 漂移检测+自动重训练<br>
3. 可复现性 → 环境+数据+代码+参数全记录(MLflow)<br>
4. 非确定性 → 固定随机种子+记录所有实验<br><br>
<b>部署策略对比：</b><br>
• 蓝绿部署：两套环境切换→零宕机但双倍资源<br>
• 金丝雀发布：5%流量→新模型，观察无问题→逐步放大<br>
• A/B测试：同时运行两个模型→统计检验哪个更好<br>
• 影子模式：新模型并行运行但不影响生产→收集数据对比`,
        code: `# 模型健康检查端点
@router.get("/model/{model_id}/health")
def model_health(model_id: str):
    model = load_model(model_id)
    # 1. 结构完整性
    if not model: return {"status":"unhealthy","reason":"model not found"}
    # 2. 延迟测试
    start = time.time()
    _ = model.predict(test_input)
    latency = (time.time()-start)*1000
    if latency > 100: return {"status":"degraded","latency_ms":latency}
    # 3. 输出合理性
    pred = model.predict(validation_input)
    if np.isnan(pred).any(): return {"status":"unhealthy","reason":"NaN output"}
    return {"status":"healthy","latency_ms":round(latency,2)}`,
        codeLang: 'python'
      },
      {
        title: '6.2 ONNX深度解析',
        content: `<b>ONNX(Open Neural Network Exchange) = ML模型的"中间表示"：</b><br>
• 计算图表示(算子级)：Conv→BatchNorm→ReLU→MaxPool...<br>
• 支持150+算子：覆盖CNN/RNN/Transformer/树模型<br>
• 跨框架：PyTorch→ONNX→ONNX Runtime(CPU/CUDA)/TensorRT/OpenVINO/CoreML<br><br>
<b>ONNX Runtime推理加速原理：</b><br>
1. 图优化：算子融合(Conv+BN→单算子)、常量折叠、死代码消除<br>
2. 内存优化：Arena分配器减少碎片、激活值重计算(省显存)<br>
3. 量化：FP32→INT8，精度损失<1%，推理速度2-4x<br><br>
<b>动态轴(Dynamic Axes)：</b>batch/sequence维度标记为动态→同一模型支持不同batch size`,
        code: `import torch
# PyTorch → ONNX 导出（动态batch size）
dummy = torch.randn(1, 3, 224, 224)
torch.onnx.export(
    model, dummy, "model.onnx",
    input_names=['input'], output_names=['output'],
    dynamic_axes={'input':{0:'batch_size'}, 'output':{0:'batch_size'}},
    opset_version=14
)
# 验证
import onnx
onnx_model = onnx.load("model.onnx")
onnx.checker.check_model(onnx_model)

# ONNX Runtime推理
import onnxruntime as ort
session = ort.InferenceSession("model.onnx")
outputs = session.run(None, {'input': input_numpy})`,
        codeLang: 'python'
      },
    ]},
    { level: 'scenario', sections: [
      {
        title: '6.3 平台部署流水线',
        content: `<b>完整部署流程：</b><br>
1. <b>模型训练</b>→自动注册到MLflow Registry<br>
2. <b>ONNX导出</b>→转换为跨平台格式+图优化<br>
3. <b>部署服务</b>→一键生成REST API端点<br>
4. <b>监控告警</b>→实时追踪R²/延迟/错误率/漂移分数<br>
5. <b>模型治理</b>→审计日志+版本对比+回滚<br><br>
<b>推理服务API格式：</b>POST /api/v1/inference/{model_id}/predict<br>
输入：{"features": [[1.7,1242,845,...]]}<br>
输出：{"predictions": [1310.5], "latency_ms": 12.3}`,
        action: { label: '🚀 打开部署管理', action: 'nav', path: '/deploy/deployment', primary: true }
      }
    ]},
  ],
  quiz: [
    { question: 'ONNX Runtime推理加速的主要手段？', options: ['增加层数', '算子融合+量化', '用更多GPU', '改模型结构'], answer: 1 },
    { question: '金丝雀发布的核心思想？', options: ['一次全量', '小流量验证→逐步扩大', '影子模式', '回滚'], answer: 1 },
  ]
},

// ═══════════════════════════════════════════
// 模块7：时序预测与异常检测
// ═══════════════════════════════════════════
{
  id: 7, icon: '⏱️', title: '时序预测与异常检测', done: false,
  desc: 'ARIMA数学→Prophet分解→LSTM/Transformer→异常检测算法→工业应用',
  layers: [
    { level: 'principle', sections: [
      {
        title: '7.1 ARIMA的数学原理',
        content: `<b>【科普】预测明天几点出太阳——人类最古老的预测问题之一</b><br><br>从古代农民看天象预测收成，到现代商业预测销量与客流，时间序列预测贯穿人类文明的每一个阶段。与普通的回归问题不同，时序数据有<b>时间依赖性</b>——今天的数据和昨天紧密相关，顺序不能随意打乱。从统计学的ARIMA，到Facebook的Prophet，再到Transformer架构的Informer，每个算法背后都凝聚了人类对「理解时间」的智慧与探索。<br><br><b>ARIMA(p,d,q) = AR(p) + I(d) + MA(q)：</b><br>
• AR(p)：yₜ=c+φ₁yₜ₋₁+...+φₚyₜ₋ₚ+εₜ → 用过去p个值预测当前<br>
• I(d)：对原始序列做d阶差分使其平稳 → Δyₜ=yₜ-yₜ₋₁<br>
• MA(q)：yₜ=μ+εₜ+θ₁εₜ₋₁+...+θqεₜ₋q → 用过去q个误差修正<br><br>
<b>平稳性检验：</b><br>
• ADF检验(Augmented Dickey-Fuller)：H₀=存在单位根(非平稳)，p<0.05拒绝H₀→平稳<br>
• KPSS检验：H₀=平稳，p<0.05拒绝H₀→非平稳。两个检验互补使用<br><br>
<b>ACF/PACF定阶法：</b><br>
• ACF(Autocorrelation)：滞后k的自相关系数→用于定MA的q<br>
• PACF(Partial ACF)：去除中间变量影响→用于定AR的p<br>
• 实战：用auto_arima(pmdarima库)自动搜索最优(p,d,q)`,
        code: `from pmdarima import auto_arima
from statsmodels.tsa.stattools import adfuller

# 平稳性检验
result = adfuller(df['value'].dropna())
print(f'ADF Statistic: {result[0]:.3f}, p-value: {result[1]:.3f}')
print('平稳' if result[1] < 0.05 else '非平稳→需要差分')

# 自动ARIMA
model = auto_arima(df['value'], seasonal=True, m=24,  # m=24小时=日周期
    trace=True, error_action='ignore', suppress_warnings=True,
    stepwise=True)  # 逐步搜索（更快，近似最优）
print(f'Best ARIMA: {model.order}, Seasonal: {model.seasonal_order}')
# 预测
forecast, conf_int = model.predict(n_periods=48, return_conf_int=True)`,
        codeLang: 'python'
      },
      {
        title: '7.2 异常检测算法家族',
        content: `<b>三大范式：</b><br><br>
<b>1. 统计方法：</b><br>
• Z-score：|z|>3→异常。假设正态分布<br>
• IQR：Q1-1.5×IQR ~ Q3+1.5×IQR → 适应偏态分布<br>
• 移动平均±kσ：适应缓慢变化的时间序列<br><br>
<b>2. 基于距离/密度：</b><br>
• LOF(局部异常因子)：比较点的局部密度与邻居。密度明显低于邻居→异常<br>
• Isolation Forest：随机选择特征和阈值分割→异常点"孤立"更快(路径更短)<br>
• DBSCAN：密度聚类→不属于任何簇的点=异常<br><br>
<b>3. 深度学习方法：</b><br>
• AutoEncoder：压缩→重构，重构误差大=异常<br>
• LSTM-AE：时序版AutoEncoder，学习正常时序模式<br>
• VAE(变分AE)：学习数据分布→计算重构概率（比重构误差更鲁棒）`,
        code: `from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor

# Isolation Forest（工业最常用）
iso = IsolationForest(contamination=0.05, random_state=42)
anomalies = iso.fit_predict(X)  # -1=异常, 1=正常

# LOF
lof = LocalOutlierFactor(n_neighbors=20, contamination=0.05)
anomalies_lof = lof.fit_predict(X)

# 自适应阈值（基于训练数据分布）
threshold = np.percentile(scores, 95)  # 95分位数作为阈值`,
        codeLang: 'python'
      },
    ]},
    { level: 'scenario', sections: [
      {
        title: '7.3 综合案例：交通流量预测',
        content: `<b>数据：</b>52,560行（15分钟/点，1年）<br>
<b>特征工程：</b>lag1/24/168(上一时刻/昨天同时刻/上周同时刻)、是否节假日、小时/月份/季节编码<br>
<b>模型：</b>LightGBM（处理表格数据+lag特征优于LSTM）<br>
<b>结果：</b>R²=0.89, MAE/峰值≈8%`,
        dataRef: '交通流量数据 — 52,560行',
        note: '🚦 <b>天气影响：</b>引入天气预报可将预测误差再降20%。恶劣天气下流量模式容易偏离→增加不确定性量化。'
      },
    ]},
    { level: 'practice', sections: [
      {
        title: '7.4 平台实操',
        content: `1. <b>训练任务</b> → 上传数据→选算法→训练→查看时序预测效果<br>
2. <b>异常检测</b> → 探索数据异常模式<br>
3. <b>Notebook</b> → 运行完整时序分析代码`,
        action: { label: '⚡ 打开训练任务', action: 'nav', path: '/model/training-tasks', primary: true }
      }
    ]}
  ],
  quiz: [
    { question: 'ARIMA中的d参数表示什么？', options: ['自回归阶数', '差分阶数(平稳化)', '滑动平均阶数', '季节周期'], answer: 1 },
    { question: 'Isolation Forest的异常检测原理？', options: ['基于距离', '基于密度', '基于树的隔离速度', '基于聚类'], answer: 2 },
  ]
},

// ═══════════════════════════════════════════
// 模块8：专项AI技术栈
// ═══════════════════════════════════════════
{
  id: 8, icon: '🎯', title: '专项AI：RL/GNN/GAN/推荐/因果', done: false,
  desc: '强化学习→图神经网络→生成对抗→推荐系统→因果推断',
  layers: [
    { level: 'principle', sections: [
      {
        title: '8.1 强化学习：MDP与Bellman方程',
        content: `<b>【科普】5个改变世界的AI前沿方向</b><br><br>强化学习让AlphaGo击败围棋世界冠军；图神经网络帮科学家发现新药物分子；生成对抗网络能创造以假乱真的图像和数据；因果推断让AI从「相关性」走向「因果性」；推荐系统驱动着电商和短视频数十亿的用户体验。这些专项AI方向虽然不像深度学习那样「大众」，但在各自领域内威力巨大——而且它们在工业场景中有着直接且迫切的应用需求。<br><br><b>MDP五元组：(S,A,P,R,γ)</b><br>
• S状态空间、A动作空间、P(s'|s,a)状态转移概率、R(s,a)即时奖励、γ∈[0,1]折扣因子<br><br>
<b>价值函数与Bellman方程：</b><br>
• 状态价值Vπ(s)=E[ΣγᵗRₜ|s₀=s,π] — 从状态s开始，按策略π走，期望累积奖励<br>
• 动作价值Qπ(s,a)=E[ΣγᵗRₜ|s₀=s,a₀=a,π]<br>
• <b>Bellman最优方程：</b>V*(s)=maxₐ[R(s,a)+γΣₛ'P(s'|s,a)V*(s')]<br>
→ 当前最优价值=即时奖励+max(下一步最优价值)×γ<br><br>
<b>算法演进：</b><br>
• Q-Learning：Q(s,a)←Q(s,a)+α[R+γmaxₐ'Q(s',a')-Q(s,a)]<br>
• DQN：用神经网络近似Q函数 + Experience Replay(打破时序相关) + Target Network(稳定训练)<br>
• PPO：限制策略更新幅度(Clip)防止破坏性更新 → 最稳定的Policy Gradient方法`,
        code: `# Q-Learning 表格法
import numpy as np
Q = np.zeros((n_states, n_actions))
for episode in range(1000):
    state = env.reset()
    for step in range(200):
        # ε-greedy 探索
        if np.random.random() < epsilon:
            action = env.action_space.sample()
        else:
            action = np.argmax(Q[state])
        next_state, reward, done, _ = env.step(action)
        # Bellman更新
        Q[state,action] += alpha * (reward + gamma*np.max(Q[next_state]) - Q[state,action])
        state = next_state
        if done: break`,
        codeLang: 'python'
      },
      {
        title: '8.2 图神经网络(GNN)消息传递范式',
        content: `<b>图的表示：</b>G=(V,E)，V节点集，E边集。邻接矩阵A∈{0,1}ⁿˣⁿ，特征矩阵X∈ℝⁿˣᵈ<br><br>
<b>消息传递公式：</b><br>
hᵤ⁽ˡ⁺¹⁾ = UPDATE(hᵤ⁽ˡ⁾, AGGREGATE({hᵥ⁽ˡ⁾: v∈N(u)}))<br>
• AGGREGATE：聚合邻居信息(求和/均值/max/注意力)<br>
• UPDATE：结合自身信息和聚合信息(concat+MLP/GRU)<br>
• l层GNN=聚合l跳邻居信息<br><br>
<b>核心架构：</b><br>
• GCN：AGGREGATE=归一化求和<br>
• GAT：AGGREGATE=注意力加权求和 → 不同邻居不同权重<br>
• GraphSAGE：AGGREGATE=采样邻居(大规模图)<br><br>
<b>工业应用：</b>电网拓扑(节点=站，边=线路)、知识图谱推理、设备依赖关系建模`,
        note: '🎯 平台GNN模块基于DGL/PyG，支持GCN/GAT/GraphSAGE，可用于设备关联分析和知识图谱推理。'
      },
      {
        title: '8.3 GAN与因果推断',
        content: `<b>GAN对抗训练：min_G max_D V(D,G)=E[logD(x)]+E[log(1-D(G(z)))]<br>
G试图最小化、D试图最大化这个目标→纳什均衡<br><br>
<b>训练不稳定问题的解决：</b><br>
• WGAN：用Wasserstein距离替代JS散度→梯度不消失<br>
• WGAN-GP：梯度惩罚替代权重裁剪→更稳定<br>
• StyleGAN：渐进训练+风格混合→高分辨率生成<br><br>
<b>工业应用：</b>数据增强(罕见缺陷样本生成)、异常检测(AnoGAN)、缺失数据填充<br><br>
<b>因果推断三层次(Pearl阶梯)：</b><br>
L1关联(Seeing)：P(y|x) — 相关性<br>
L2干预(Doing)：P(y|do(x)) — 因果效应<br>
L3反事实(Imagining)：如果当时选了另一方案会怎样？`,
        code: `import dowhy
from dowhy import CausalModel

# 定义因果图(DAG)
model = CausalModel(data=data, treatment='节能改造', outcome='能耗降低',
    graph="digraph{气温->能耗; 产量->能耗; 气温->产量; 节能改造->能耗; 产量->节能改造}")
# 识别
identified = model.identify_effect(proceed_when_unidentifiable=True)
# 估计（backdoor调整）
estimate = model.estimate_effect(identified, method_name="backdoor.linear_regression")
print(f'因果效应(ATE): {estimate.value:.3f}')
# 反驳（敏感性分析）
refute = model.refute_estimate(identified, estimate, method_name="random_common_cause")
print(f'Refutation p-value: {refute.refutation_result["p_value"]:.3f}')`,
        codeLang: 'python'
      },
    ]},
    { level: 'practice', sections: [
      {
        title: '8.4 平台专项AI入口',
        content: `模型工厂包含所有专项AI训练模块：<b>RL训练</b>(DQN/PPO/A2C)、<b>GNN训练</b>(GCN/GAT)、<b>GAN训练</b>、<b>因果分析</b>、<b>推荐系统</b>。每个模块预置模板+真实数据集。`,
        action: { label: '🎮 打开模型工厂', action: 'nav', path: '/model/training-tasks', primary: true }
      }
    ]}
  ],
  quiz: [
    { question: 'DQN中的Experience Replay解决什么问题？', options: ['加速训练', '打破时序相关性', '减少内存', '增加探索'], answer: 1 },
    { question: 'GNN的消息传递范式包含哪两步？', options: ['卷积+池化', 'AGGREGATE+UPDATE', 'Encoder+Decoder', 'Forward+Backward'], answer: 1 },
  ]
},

// ═══════════════════════════════════════════
// 模块9：多模态AI
// ═══════════════════════════════════════════
{
  id: 9, icon: '🌐', title: '多模态AI：视觉·语音·文本', done: false,
  desc: 'CV分类检测分割生成→OCR→ASR/TTS→多模态融合→工业落地',
  layers: [
    { level: 'principle', sections: [
      {
        title: '9.1 CV四大任务深度对比',
        content: `<b>【科普】让机器「看懂」世界——多模态AI的时代到来了</b><br><br>人类通过多种感官理解世界：眼睛看、耳朵听、手触摸。多模态AI的目标就是让机器也具备这种综合感知能力。从自动驾驶(摄像头+激光雷达+GPS融合)到智能安防(视频+音频+传感器)，从医疗诊断(CT+MRI+病历文本)到工业检测(视觉+声音+振动数据)——多模态AI正在让机器获得接近乃至超越人类的感知能力。<br><br><b>图像分类(Classification)：</b>输出=类别标签。Backbone(ResNet/EfficientNet)+分类头(全连接)。IoU不关心位置。<br>
<b>目标检测(Detection)：</b>输出=多个bbox+类别。Anchor-based(YOLO/Faster R-CNN) vs Anchor-free(CenterNet/FCOS)。mAP@0.5为主要指标。<br>
<b>语义分割(Segmentation)：</b>输出=每个像素的类别。Encoder-Decoder结构(U-Net)+跳跃连接恢复空间细节。mIoU为指标。<br>
<b>图像生成(Generation)：</b>文本→图像。Stable Diffusion = VAE潜空间+UNet去噪+CLIP文本编码。<br><br>
<b>YOLO核心思想：</b>图像分S×S网格→每个网格预测B个bbox(5+C个值：x,y,w,h,conf+C类)→NMS去重。单阶段→速度快。`,
        code: `# Stable Diffusion 生成
from diffusers import StableDiffusionPipeline
pipe = StableDiffusionPipeline.from_pretrained("runwayml/stable-diffusion-v1-5")
pipe = pipe.to("cuda")
image = pipe("Industrial factory with solar panels, digital art").images[0]`,
        codeLang: 'python'
      },
      {
        title: '9.2 语音AI与OCR技术栈',
        content: `<b>ASR(自动语音识别)架构：</b><br>
Whisper(OpenAI)：Encoder-Decoder Transformer，输入=log-Mel频谱，输出=文本tokens。68万小时多语言训练。<br>
• 支持99种语言，抗噪声能力强<br>
• whisper-tiny(39M参数)到whisper-large(1.5B)<br><br>
<b>TTS(文字转语音)：</b><br>
SpeechT5：统一框架处理语音识别/合成/转换。Encoder(text→hidden)+Decoder(hidden→mel-spectrogram)+Vocoder(mel→waveform)<br><br>
<b>OCR(EasyOCR)流程：</b><br>
1. CRAFT文本检测(字符级heatmap)<br>
2. CRNN+CTC/Attention文字识别<br>
3. 支持80+语言，中文识别率>95%`,
        note: '🎨 平台CV/OCR/Audio模块已全部集成。CV支持20+预训练模型，OCR支持10语种+表格提取，Audio支持ASR/TTS/分类。'
      },
    ]},
    { level: 'scenario', sections: [
      {
        title: '9.3 工业多模态应用全景',
        content: `<b>CV：</b>产品缺陷检测(ResNet迁移学习)、设备状态识别(YOLO)、安全合规检测<br>
<b>OCR：</b>仪表读数自动录入、纸质工单数字化、铭牌信息提取<br>
<b>ASR+TTS：</b>语音控制设备操作、安全告警语音播报、巡检语音记录转文字<br>
<b>文生图：</b>设备状态示意图自动生成、缺陷样本数据增强<br><br>
<b>多模态融合：</b>图像(设备外观)+声音(运行异常噪音)+振动数据(时序)→综合判断设备健康状态`,
        modelRef: 'ResNet18/MobileNetV2 | YOLO | Whisper | SpeechT5 | EasyOCR | Stable Diffusion'
      }
    ]},
    { level: 'practice', sections: [
      {
        title: '9.4 平台多模态入口',
        content: `1. <b>CV训练：</b>分类/检测/分割/生成四大任务<br>
2. <b>OCR：</b>上传图片→自动识别文本+表格<br>
3. <b>音频：</b>语音转文字/文字转语音/音频分类<br>
4. <b>文生图：</b>输入描述→生成工业场景图像`,
        action: { label: '🖼️ CV训练（暂不可用）', action: 'nav', path: '/model/cv-training', primary: true, available: false, unavailableHint: 'CV训练当前处于不可用状态；本节可继续学习原理，实操请使用已开放的算法商店或代码编辑器。' }
      }
    ]}
  ],
  quiz: [
    { question: 'YOLO和Faster R-CNN的主要区别？', options: ['相同', '单阶段vs两阶段', '只支持分类', 'YOLO更准确但更慢'], answer: 1 },
  ]
},
];