# Pulse → L2 数据契约对齐

> **文档性质**:接入层规约 / 数据契约文档
> **目标读者**:任何要写"读 Pulse 数据"代码的人(含 LLM)
> **更新规则**:Pulse schema 演化时同步更新;Phase 2 起由 Librarian v1 自动维护
> **位置**:`/opt/accelerator/knowledge/pulse/SCHEMA_NOTES.md`(knowledge mirror 的人类决策版)

---

## 1. 总体原则

### 1.1 Pulse 数据建模哲学:稳定行 + 可演化 jsonb

Pulse 用的是 **"稳定行 + 可演化 jsonb 载荷"** 模式:

- **稳定的事实**(id、外键、状态、时间戳)→ 顶层列
- **会演化的属性**(模型版本、平台细节、生成元数据)→ jsonb 字段

这个选择对 Pulse 自身合理,但对 L2 接入意味着:**L2 不能假装 jsonb 里的 key 是顶层列**,必须显式从 jsonb 路径取值。

### 1.2 L2 Connector 的两层职责

```
Pulse 真实结构 (顶层 + jsonb)
       │
       │  1. 反腐败层:把 jsonb 关键字段抽取出来
       │
       ▼
L2 内部 dataclass(扁平化的、稳定的、L2 内部使用的模型)
```

Connector 做两件事,在 spec 里**必须分开**:

1. **SQL 查询**:只 SELECT Pulse **实际存在**的列名
2. **抽取映射**:从顶层列 + jsonb 字段构造 L2 内部 dataclass

### 1.3 "已知空通道"vs "已知不存在"

Pulse 里有些字段**存在但全空**(如 `publishes.metrics={}`),有些字段**完全不存在**(如 `publishes.last_synced_at`)。

| 类型 | 处理 |
|---|---|
| **已知空通道** | Connector 正常拉取(空 dict 也搬),L2 dataclass 字段保留 |
| **已知不存在** | dataclass 不要这个字段,删除或改名 |

**不要**为不存在的字段写默认值——会掩盖"这个数据 Pulse 还没产生"的事实。

---

## 2. articles 表

### 2.1 真实结构(权威基线,2026-04-30)

```
articles (12 列)
─────────────────────────────────────────────────
顶层稳定列:
  id                  uuid       PK
  title               text       NN
  content             text       
  status              text       
  topic_id            uuid       FK → topics.id
  cover_url           text       (兼容遗迹,Pulse 多平台改造后基本全空)
  tenant_id           text       FK → tenants.id
  created_at          timestamptz
  updated_at          timestamptz

jsonb 演化载荷:
  versions            jsonb      生成元数据
  platform_versions   jsonb      多平台特化版本
  compliance_check    jsonb      合规检查 + 角度
```

### 2.2 jsonb 字段实际 key 结构

**`versions`**(生成元数据):
```json
{
  "word_count": 1896,
  "model_used": "claude-sonnet",
  "language": "zh",
  ...
}
```

**`platform_versions`**(多平台封面 + 可能的平台特化内容):
```json
{
  "cover_wechat": "https://...",
  "cover_xhs": "https://...",
  "cover_toutiao": "https://...",
  ...
}
```

**`compliance_check`**(合规结果 + angle):
```json
{
  "angle": "从综合角度...",
  ...
}
```

### 2.3 → L2 PulseArticle dataclass 映射

| L2 dataclass 字段 | 来源 | 类型 | 备注 |
|---|---|---|---|
| `id` | `articles.id` | str | UUID 转 str |
| `title` | `articles.title` | str | |
| `content_summary` | `articles.content[:200]` | str | L2 截断,不存全文 |
| `status` | `articles.status` | str | |
| `topic_id` | `articles.topic_id` | str \| None | UUID 转 str |
| `tenant_id` | `articles.tenant_id` | str | Phase 1 仅 'history' |
| `word_count` | `articles.versions->>'word_count'` | int \| None | jsonb 路径取值 + 转 int |
| `model_used` | `articles.versions->>'model_used'` | str \| None | |
| `language` | `articles.versions->>'language'` | str \| None | |
| `angle` | `articles.compliance_check->>'angle'` | str \| None | ★ 概念归属:angle 属于 article,不是 topic |
| `platform_versions` | `articles.platform_versions` | dict | jsonb 原样保留,Watcher 按平台拆 |
| `created_at` | `articles.created_at` | datetime | |
| `updated_at` | `articles.updated_at` | datetime | |

### 2.4 删除的字段(SPEC 错猜)

- `cover_url` 顶层列实际全空,**不放进 dataclass**——按平台从 `platform_versions` 取

### 2.5 已知空通道

无。

---

## 3. publishes 表

### 3.1 真实结构(权威基线,2026-04-30)

```
publishes (11 列)
─────────────────────────────────────────────────
顶层列:
  id                    uuid       PK
  article_id            uuid       FK → articles.id
  platform              text       NN
  status                text       
  url                   text       
  platform_article_id   text       
  published_at          timestamptz
  metrics               jsonb      平台数据(KPI 主源)
  error_message         text       
  tenant_id             text       FK → tenants.id
  created_at            timestamptz
```

### 3.2 → L2 PulsePublish dataclass 映射

| L2 dataclass 字段 | 来源 | 类型 | 备注 |
|---|---|---|---|
| `id` | `publishes.id` | str | |
| `article_id` | `publishes.article_id` | str | |
| `platform` | `publishes.platform` | str | wechat/xhs/toutiao/baijiahao |
| `status` | `publishes.status` | str | |
| `url` | `publishes.url` | str \| None | |
| `platform_article_id` | `publishes.platform_article_id` | str \| None | |
| `published_at` | `publishes.published_at` | datetime \| None | |
| `metrics` | `publishes.metrics` | dict | jsonb 原样保留(空 {} 也搬) |
| `error_message` | `publishes.error_message` | str \| None | |
| `tenant_id` | `publishes.tenant_id` | str | |
| `created_at` | `publishes.created_at` | datetime | |

### 3.3 删除的字段(SPEC 错猜)

- `last_synced_at` 不存在,删除

### 3.4 已知空通道 ⚠️

**`publishes.metrics` 当前所有行 = `{}`**

含义:Pulse 还没实现"平台数据回流"管道。Connector **必须正常拉取**(即便空 dict),Watcher 写 ops_metrics 时**必须把空 metrics 也写进去**。

理由:这是 L2 存在的核心理由之一。Phase 2 起会有补数据 worker(直连微信公众号 API / 头条 API 等)往 ops_metrics 里填真实数据,**通道现在就要建好**。

> 设计上的关键:**ops_metrics 里的 `metrics` 字段不依赖 Pulse 是否填了 publishes.metrics**。Phase 2 之后,数据会从两条路径进 ops_metrics——Pulse 的 publishes.metrics(目前空)+ L2 自己的平台 API worker(待开发)。

---

## 4. topics 表

### 4.1 真实结构(权威基线,2026-04-30)

```
topics (9 列)
─────────────────────────────────────────────────
  id           uuid         PK
  title        text         NN
  category     text         
  outline      jsonb        选题大纲(结构未深查)
  priority     int          
  status       text         
  tenant_id    text         FK → tenants.id
  created_at   timestamptz
  updated_at   timestamptz
```

### 4.2 → L2 PulseTopic dataclass 映射

| L2 dataclass 字段 | 来源 | 类型 | 备注 |
|---|---|---|---|
| `id` | `topics.id` | str | |
| `title` | `topics.title` | str | |
| `category` | `topics.category` | str \| None | |
| `priority` | `topics.priority` | int \| None | |
| `status` | `topics.status` | str | |
| `tenant_id` | `topics.tenant_id` | str | |
| `outline` | `topics.outline` | dict | jsonb 原样保留(Phase 1 不深用) |
| `created_at` | `topics.created_at` | datetime | |
| `updated_at` | `topics.updated_at` | datetime | |

### 4.3 删除的字段(SPEC 错猜)

- **`angle` ⚠️ 重大归属错位**:此字段不属于 topic,而属于 article。原 SPEC 把 angle 挂在 PulseTopic 上是错误的概念建模。

> **修订**:`angle` 移到 `PulseArticle`,从 `articles.compliance_check->>'angle'` 取。
> 蓝图 §12 术语表已加入此澄清。

### 4.4 已知空通道

无。

---

## 5. interactions 表

### 5.1 真实结构(权威基线,2026-04-30)

```
interactions (10 列)
─────────────────────────────────────────────────
  id                uuid         PK
  publish_id        uuid         FK → publishes.id
  interaction_type  text         
  content           text         
  reply_by          text         (回复者,而非原评论者用户名)
  reply_content     text         
  replied_at        timestamptz  (回复时间,可推断"是否已回复")
  user_profile      jsonb        
  tenant_id         text         FK → tenants.id
  created_at        timestamptz
```

### 5.2 Phase 1 处理:**不接入**

理由:
1. SPEC 假设的 4 个字段(`platform`, `user_name`, `sentiment`, `replied`)中,3 个不存在,1 个名字+语义不一致
2. 表当前 0 行,即便 Connector 正确也没数据可拉
3. 这张表的真实使用场景在 Phase 3 (Facilitator) 才出现
4. 修这张表的成本 > 推迟的成本

### 5.3 推迟到 Phase 2/3 启动前再处理时,需做的事:

- `platform` 必须 JOIN publishes 拿(因为 interactions 没这列)
- `user_name` 字段不存在;最相近的是 `reply_by`,但语义是"回复者"(可能是博主自己),不是"评论用户"
- `sentiment` 不存在,需要 L2 自己接情感分析或从 user_profile jsonb 看有没有
- `replied` 不存在,但 `replied_at IS NOT NULL` 可以推断回复状态

### 5.4 Phase 1 的 Connector 接口处理

`PulseConnector.get_interactions_by_date()` 方法**保留签名,实现 raise NotImplementedError**——给未来 Phase 2 留接口位,同时显式拒绝调用。

---

## 6. configs 表(参考,Phase 1 不接入)

Pulse 用 configs 表做键值配置存储(包括 daily_metrics 等)。Phase 1 Watcher 不读它。

Librarian v0 镜像它的 schema 是为了 Phase 2 Analyst 读元数据(如"Pulse 当前 daily_metrics 有哪些 key")。

---

## 7. 跨表概念澄清

### 7.1 angle 的归属

| ❌ 错误模型(SPEC 初版) | ✅ 正确模型(本文档) |
|---|---|
| topic 拥有 angle(一对一) | article 选择 angle(每文一选) |
| 同一 topic 一个 angle | 同一 topic 可有多文章,每篇 angle 不同 |

### 7.2 platform 的归属

`platform` **只在 publishes 上是顶层列**。articles 上没有 platform(一篇文章可发多平台);interactions 上没有 platform(要 JOIN publishes 拿)。

任何 L2 代码里写 "interaction.platform" 都是错的。

### 7.3 cover 的归属

| 旧模型 | 新模型 |
|---|---|
| `articles.cover_url` 一文一封面 | `articles.platform_versions->>'cover_<plat>'` 一文一平台一封面 |

`articles.cover_url` 顶层列保留为兼容遗迹但全空,**不要从这里取数据**。

---

## 8. 变更日志

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-04-30 | 初版,基于 Phase 1 Step 3 真实 schema 探查;修订 angle 归属、删除 SPEC 中错猜字段、记录已知空通道 | Pulse PG 13.23 真实 \d 输出 + row_to_json 样本 |

---

## 9. 维护规则

- **Phase 1 阶段**:本文档由人类手动维护(每次 Pulse 改 schema 时)
- **Phase 2 阶段起**:Librarian v1 升级时,自动产出 `extracted/schema_alignment.md`,本文档仍保留作为人类决策记录
- **本文档跟 `schema/articles.md` 等机器产出的区别**:那些是字段级元数据(类型/默认值/索引),本文档是**语义级解释 + L2 内部映射决策**
- **Pulse schema 任何变更**:必须更新本文档 §8 变更日志,且评估是否需要改 Connector / Watcher / Librarian
