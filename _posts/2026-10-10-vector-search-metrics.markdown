---
layout: post
title: "深入理解向量检索：L2 Distance、Cosine Similarity 与 Inner Product"
date: 2026-10-10 12:00:00 +0800
author: "weak old dog"
header-img-credit: false
title-wrap: true
mathjax: true
tags:
    - 数据处理
---

向量检索里经常会遇到三个名字：**L2 Distance（欧氏距离）**、**Cosine Similarity（余弦相似度）**、**Inner Product（内积）**。它们都能用来排列候选文档，但回答的问题不同：两个点有多远，两个方向有多像，一个向量在另一个方向上的投影有多大。

最容易混淆的一点是：**它们的公式不同，但在满足条件时，检索排序可以相同。** 这种数学上的等价，也不意味着换一种 ANN 索引配置，就一定返回完全相同的结果。

这篇文章先用二维图把几何关系讲清楚，再推导归一化后的等价关系，最后放到 RAG 的检索链路里，看距离度量、ANN、RRF 和 reranker 各自解决什么问题。图中的坐标用于解释数学，不代表真实 embedding 的某两个维度具有可解释的语义。

## 1. 向量检索到底在比较什么

把查询编码成 $q\in\mathbb{R}^d$，把文档片段编码成 $x\in\mathbb{R}^d$。检索器根据一个距离或相似度函数，从候选集合中选出 Top-K。

这里的 $d$ 是向量维度，$K$ 是返回数量。**距离通常越小越好，相似度通常越大越好。** API 返回的字段即使都叫 `score`，也未必具有相同方向或单位。

| 度量 | 定义 | 几何直觉 | 排序方向 | 是否受向量长度影响 |
|---|---|---|---|---|
| L2 Distance | $\lVert q-x\rVert_2$ | 两个终点之间的直线距离 | 升序 | 是 |
| Squared L2 | $\lVert q-x\rVert_2^2$ | 直线距离的平方 | 升序 | 是 |
| Cosine Similarity | $\dfrac{q^\top x}{\lVert q\rVert_2\lVert x\rVert_2}$ | 两个向量的方向一致程度 | 降序 | 对正比例缩放不变 |
| Inner Product | $q^\top x$ | 有符号投影与查询长度的乘积 | 降序 | 是 |

L2 是严格意义上的距离；cosine 和 inner product 是相似度。向量数据库有时统一用 `metric` 或 `distance` 来命名配置项，不能据此推断它们都满足数学上的距离公理。

更根本的一层是：embedding 模型把哪些关系编码为“近”，取决于模型的训练目标与输入方式。换一种距离公式，不能自动补上模型没有学到的语义关系。

## 2. L2 Distance：比较两个终点的位置

### 2.1 从勾股定理到高维空间

对两个实向量 $q=(q_1,\ldots,q_d)$、$x=(x_1,\ldots,x_d)$：

$$
d_2(q,x)=\lVert q-x\rVert_2
=\sqrt{\sum_{j=1}^{d}(q_j-x_j)^2}.
$$

二维时就是勾股定理。假设 $q=(1,2)$、$x=(4,6)$，横向相差 3，纵向相差 4：

$$
d_2(q,x)=\sqrt{(1-4)^2+(2-6)^2}=5.
$$

![欧氏距离示意图：q=(1,2) 与 x=(4,6) 的终点构成一条长为 5 的线段，横向差 3、纵向差 4。]({{ '/pics/vector-search-metrics/l2-distance.svg' | relative_url }}){:height="60%" width="60%"}

*图 1：向量从原点出发，L2 比较的是两个终点之间的距离。图中的坐标轴使用相同刻度。*

距离为 0 表示向量完全相同。距离增大表示位置更远，但是否意味着文本相关性更低，还要看 embedding 空间的性质。

### 2.2 为什么很多系统返回的是 L2 的平方

因为平方根在非负数上严格单调递增：

$$
d_2(q,x_a)<d_2(q,x_b)
\iff d_2^2(q,x_a)<d_2^2(q,x_b).
$$

如果只需要排序，可以省掉开方。不过，**排序相同不代表数值相同**：上面的例子返回欧氏距离是 5，返回 squared L2 则是 25。

[Faiss 官方说明](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances)中，`METRIC_L2` 返回 squared L2。[Milvus 的 L2 说明](https://milvus.io/docs/metric.md)也明确省略平方根。设定过滤阈值时，应先核对实际返回值；“距离不超过 5”对应 squared L2 不超过 25。

## 3. Cosine Similarity：比较方向，忽略正比例缩放

### 3.1 余弦的定义

对**两个非零向量**，余弦相似度为：

$$
s_{\cos}(q,x)
=\frac{q^\top x}{\lVert q\rVert_2\lVert x\rVert_2}
=\cos\theta.
$$

这里 $q^\top x=\sum_{j=1}^{d}q_jx_j$ 是内积，$\theta\in[0,\pi]$ 是夹角。由柯西—施瓦茨不等式，相似度位于 $[-1,1]$：

| 夹角 | Cosine | 几何关系 |
|---|---|---|
| $0^\circ$ | 1 | 同向 |
| $90^\circ$ | 0 | 正交 |
| $180^\circ$ | -1 | 反向 |

这些是几何关系，不能直接翻译成“文本相同”“完全不相关”“意思相反”。尤其是负余弦，不等于自然语言中的逻辑否定。

### 3.2 同方向，也可能相距很远

取 $q=(1,1)$、$x=(10,10)$：

$$
\begin{aligned}
s_{\cos}(q,x)&=1,\\
d_2(q,x)&=\sqrt{162}=9\sqrt{2}\approx12.728.
\end{aligned}
$$

两个向量方向相同，长度不同。L2 能看见终点的位移，cosine 则忽略这种正比例缩放。

更一般地，对 $\alpha>0$、$\beta>0$：

$$
s_{\cos}(\alpha q,\beta x)=s_{\cos}(q,x).
$$

“忽略长度”并不包括随意改变符号：乘以负数会翻转方向，也会改变余弦的符号。

### 3.3 Cosine distance 是不是严格的距离

有些库把 $1-s_{\cos}$ 称为 cosine distance。它便于表达“越小越相似”，但通常不满足三角不等式。例如取方向角为 $0^\circ$、$60^\circ$、$120^\circ$ 的三个单位向量：

$$
\begin{aligned}
1-\cos120^\circ&=1.5\\
&> (1-\cos60^\circ)+(1-\cos60^\circ)=1.
\end{aligned}
$$

在单位球面上，角距离 $\arccos(s_{\cos})$ 和弦长 $\sqrt{2-2s_{\cos}}$ 则是距离。它们与 cosine 的最近邻排序一致，但数值和几何意义不同。

零向量没有方向，余弦相似度无定义。工程上不要仅靠分母加一个小常数把错误输入掩盖过去：应明确拒绝、排除或单独处理这些向量。

## 4. Inner Product：方向与长度一起参与

### 4.1 投影解释

实向量的 inner product，也叫 dot product：

$$
s_{\mathrm{IP}}(q,x)=q^\top x
=\sum_{j=1}^{d}q_jx_j
=\lVert q\rVert_2\lVert x\rVert_2\cos\theta.
$$

令 $\hat q=q/\lVert q\rVert_2$。$x$ 在查询方向上的有符号投影长度为 $x^\top\hat q$，因此：

$$
q^\top x=\lVert q\rVert_2\,(x^\top\hat q).
$$

![余弦与内积示意图：q=(4,0)，x=(3,4)，x 在 q 方向的投影长度为 3，夹角约 53.13 度，cosine=0.6，内积=12。]({{ '/pics/vector-search-metrics/cosine-inner-product.svg' | relative_url }}){:height="60%" width="60%"}

*图 2：cosine 看夹角；inner product 还保留长度。投影落在反方向时，内积可以为负。*

图中 $q=(4,0)$、$x=(3,4)$，模长分别为 4 和 5，投影长度为 3：

$$
q^\top x=4\times3=12,\qquad
s_{\cos}(q,x)=\frac{12}{4\times5}=0.6.
$$

把文档向量放大两倍，夹角不变，cosine 仍为 0.6，内积则变为 24。**内积越大不一定意味着夹角越小。** 当夹角是钝角时，增大文档模长还会让负内积变得更小。

### 4.2 固定查询时，真正影响排序的是哪些量

一次检索中 $q$ 固定，$\lVert q\rVert_2$ 对所有文档都是相同的正因子。因此，仅把非零查询归一化，不会改变最大内积检索（MIPS）的排序，但会改变分数与阈值语义。

文档模长却可能不同，所以仅归一化查询，不能一般地把 IP 变成 cosine 排序。是否保留文档模长，应遵循模型训练目标；不要假定模长必然代表“信息更多”或“质量更高”。

## 5. 一个例子：三个度量，三种第一名

设查询 $q=(1,0)$，三个文档向量为：

$$
\begin{aligned}
x_A&=(0.8,0.1),\\
x_B&=(10,0),\\
x_C&=(20,5).
\end{aligned}
$$

| 文档 | L2 Distance ↓ | Cosine ↑ | Inner Product ↑ |
|---|---|---|---|
| A | $\sqrt{0.05}\approx0.2236$ | $0.8/\sqrt{0.65}\approx0.9923$ | 0.8 |
| B | 9 | 1 | 10 |
| C | $\sqrt{386}\approx19.6469$ | $20/\sqrt{425}\approx0.9701$ | 20 |

于是三种排序分别是：

- **L2：A → B → C**。A 的终点距离查询最近。
- **Cosine：B → A → C**。B 与查询完全同向。
- **IP：C → B → A**。C 在查询方向上的投影最大。

![归一化前后的候选排序：原始向量的 L2、cosine、IP 第一名分别是 A、B、C；单位化后，三种度量都按 B、A、C 排序。]({{ '/pics/vector-search-metrics/normalization-ranking.svg' | relative_url }}){:height="60%" width="60%"}

*图 3：左图保留长度，右图把所有非零向量投到单位圆上。左右两图分别使用等比例坐标，但缩放比例不同；右图局部放大展示三个候选的方向。*

这个例子说明：没有检查归一化条件时，不能直接说“L2、cosine、IP 都差不多”。

## 6. L2 归一化之后，为什么排序等价

### 6.1 把向量放到单位球面上

L2 normalization 是把每个非零向量除以自己的 L2 范数：

$$
\hat q=\frac{q}{\lVert q\rVert_2},\qquad
\hat x=\frac{x}{\lVert x\rVert_2},\qquad
\lVert\hat q\rVert_2=\lVert\hat x\rVert_2=1.
$$

二维时终点落在单位圆上，高维时落在单位球面上。方向保留，原始模长被移除。

注意：这是**逐个向量单位化**，不同于按维度做 z-score 标准化，也不同于 min-max 缩放。

### 6.2 展开平方，得到核心恒等式

先对任意向量展开：

$$
\begin{aligned}
\lVert q-x\rVert_2^2
&=(q-x)^\top(q-x)\\
&=q^\top q+x^\top x-2q^\top x\\
&=\lVert q\rVert_2^2+\lVert x\rVert_2^2-2q^\top x.
\end{aligned}
$$

代入单位向量：

$$
\begin{aligned}
\lVert\hat q-\hat x\rVert_2^2
&=1+1-2\hat q^\top\hat x\\
&=2-2s_{\cos}(q,x).
\end{aligned}
$$

因此：

$$
\boxed{
\begin{aligned}
s_{\mathrm{IP}}(\hat q,\hat x)&=s_{\cos}(q,x),\\
d_2^2(\hat q,\hat x)&=2-2s_{\cos}(q,x).
\end{aligned}
}
$$

cosine 越大，IP 越大，squared L2 越小；开平方保持距离排序。因此，对**同一候选集合、精确计算和一致的并列处理规则**：

$$
\begin{aligned}
\underset{x}{\arg\max}\ s_{\cos}(q,x)
&=\underset{x}{\arg\max}\ \hat q^\top\hat x\\
&=\underset{x}{\arg\min}\ \lVert\hat q-\hat x\rVert_2.
\end{aligned}
$$

这里等价的是**排序**。IP 与 cosine 的单位向量分数相同，L2 分数则需要转换。

### 6.3 从夹角看弦长

单位圆上，两个终点间的线段是一条弦：

$$
d_2(\hat q,\hat x)=\sqrt{2-2\cos\theta}
=2\sin\frac{\theta}{2},\qquad 0\le\theta\le\pi.
$$

![单位圆上的等价关系：q̂=(1,0)，x̂=(0.6,0.8)，夹角约 53.13 度，内积与余弦均为 0.6，弦长平方为 0.8。]({{ '/pics/vector-search-metrics/unit-circle-equivalence.svg' | relative_url }}){:height="60%" width="60%"}

*图 4：夹角越小，余弦越大，弦越短。相同的几何关系可以用三种分数描述。*

图中 $\hat q=(1,0)$、$\hat x=(0.6,0.8)$，有：

$$
\begin{aligned}
\hat q^\top\hat x&=0.6,\\
d_2^2&=(1-0.6)^2+(0-0.8)^2=0.8,\\
d_2&=\sqrt{0.8}\approx0.8944.
\end{aligned}
$$

### 6.4 阈值也可以转换，但条件不能省

对单位向量，若要求 cosine 至少为 $\tau\in[-1,1]$：

$$
s_{\cos}\ge\tau
\iff d_2^2\le2-2\tau
\iff d_2\le\sqrt{2-2\tau}.
$$

例如 cosine ≥ 0.8，对应 squared L2 ≤ 0.4，或 L2 ≤ 0.6325（约数）。这个转换不能直接用于未归一化向量，也不能直接用于包装层另行变换过的 `relevance_score`。

### 6.5 两边都单位化是充分条件，还能不能放宽

可以。对固定的非零查询，如果所有文档模长都是同一个 $c>0$，那么：

$$
\begin{aligned}
q^\top x&=c\lVert q\rVert_2s_{\cos}(q,x),\\
\lVert q-x\rVert_2^2&=\lVert q\rVert_2^2+c^2-2q^\top x.
\end{aligned}
$$

三种排序仍然等价，查询不必为单位长度。但只有两边都为单位向量时，才能直接使用前面的 $d_2^2=2-2\cos\theta$ 与阈值转换。

在工程上，同时单位化查询与文档，是更容易检查和维护的充分条件。只做查询归一化、或者只验证平均模长接近 1，都不能替代逐个候选的条件。

## 7. 用代码验证：先建立精确检索基线

下面的例子只依赖 NumPy，使用前面的三个候选验证原始排序、归一化排序与恒等式：

```python
import numpy as np

q = np.array([1.0, 0.0], dtype=np.float64)
docs = np.array([[0.8, 0.1], [10.0, 0.0], [20.0, 5.0]])
names = np.array(["A", "B", "C"])

def unit_rows(a):
    a = np.asarray(a, dtype=np.float64)
    norms = np.linalg.norm(a, axis=-1, keepdims=True)
    if not np.isfinite(a).all() or not np.isfinite(norms).all() or (norms == 0).any():
        raise ValueError("向量必须有限且非零")
    return a / norms

qn, dn = unit_rows(q), unit_rows(docs)
cosine = dn @ qn
raw_l2 = np.linalg.norm(docs - q, axis=1)
raw_ip = docs @ q
unit_l2_sq = np.sum((dn - qn) ** 2, axis=1)
unit_ip = dn @ qn

def order(scores, descending=False):
    key = -scores if descending else scores
    return names[np.argsort(key, kind="stable")].tolist()

print("原始 L2:", order(raw_l2))                       # A, B, C
print("原始 cosine:", order(cosine, descending=True))  # B, A, C
print("原始 IP:", order(raw_ip, descending=True))      # C, B, A
print("单位 L2²:", order(unit_l2_sq))                  # B, A, C
print("单位 IP:", order(unit_ip, descending=True))     # B, A, C
assert np.allclose(unit_l2_sq, 2.0 - 2.0 * cosine)
```

若使用 Faiss，可以用两个 Flat 索引验证相同数据上的等价关系。`IndexFlatIP`、`IndexFlatL2` 都做穷举检索，适合作为基线；Flat 的“精确”仍受浮点精度和并列规则影响。参见 [Faiss 索引说明](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)。

```python
import faiss

# 复用上一段的 qn、dn；Faiss 使用连续的 float32 数组。
xb = np.ascontiguousarray(dn, dtype=np.float32)
xq = np.ascontiguousarray(qn.reshape(1, -1), dtype=np.float32)

ip_index = faiss.IndexFlatIP(xb.shape[1])
l2_index = faiss.IndexFlatL2(xb.shape[1])
ip_index.add(xb)
l2_index.add(xb)

ip_scores, ip_ids = ip_index.search(xq, 3)
l2_sq, l2_ids = l2_index.search(xq, 3)
assert np.array_equal(ip_ids, l2_ids)  # 本例没有并列候选
assert np.allclose(l2_sq, 2.0 - 2.0 * ip_scores, atol=1e-6)
```

真实数据的分数可能非常接近。比较两个系统时，不能只看 ID 是否逐项一致，还要核对误差容限和边界上的并列候选。

## 8. RAG / ANN 工程实践：等价公式以外的事情

### 8.1 先对齐 embedding 与索引契约

选择度量时，优先核对模型的官方说明：是否要求 query/document 使用不同前缀或不同编码入口，输出是否已经归一化，训练目标对应 cosine、IP 还是其他方式。

查询和文档必须来自兼容的向量空间，维度相同只是必要条件。模型版本、文本预处理或输入约定改变后，应重新验证并按需要重建文档向量与索引，不能把旧文档向量和新查询向量随意混用。

| 系统 | 配置或返回值中需要核对的点 |
|---|---|
| Faiss | IP 可用于归一化向量的 cosine 检索；归一化由调用方负责；L2 返回平方距离 |
| Milvus | 常见浮点向量度量包括 `L2`、`IP`、`COSINE`；L2 省略开方；度量必须与所选索引支持范围匹配 |
| Qdrant | `Cosine` 使用归一化向量的点积实现，上传时自动归一化；不要把这一行为套用到 `Dot` 配置 |

以上行为可分别核对 [Faiss 度量文档](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances)、[Milvus 度量文档](https://milvus.io/docs/metric.md)和 [Qdrant collection 文档](https://qdrant.tech/documentation/manage-data/collections/)。部署时还应核对使用的版本与 SDK 包装层。

### 8.2 度量决定目标，ANN 决定如何近似寻找

度量回答“什么算近”，索引回答“怎样高效找到近邻”。ANN（Approximate Nearest Neighbor）通过减少搜索范围或压缩表示，换取速度和内存效率。

例如 HNSW 在图上搜索，IVF 只搜索部分倒排桶，PQ 使用压缩后的向量编码。常见的 HNSW `efSearch`、IVF `nprobe` 控制搜索工作量；具体参数含义与设置方式见 [Faiss FAQ](https://github.com/facebookresearch/faiss/wiki/FAQ)。

**单位向量上的精确排序等价，不保证两套 ANN 配置返回完全相同的 Top-K。** 候选搜索范围、建图过程、训练和量化误差、浮点精度、过滤策略、并列处理都可能影响结果。归一化之后再做有损量化，重建向量也不一定仍然具有单位模长。

实践中应先用原始向量建立精确基线，再用同一批查询测 ANN 的召回率：

$$
\operatorname{Recall@K}_{\mathrm{ANN}}
=\frac{|A_K\cap E_K|}{K},
$$

其中 $E_K$ 是精确检索 Top-K，$A_K$ 是 ANN Top-K；此处假设至少有 K 个有效候选，按查询计算后再取平均。这个指标衡量的是**对精确近邻的逼近程度**，不是业务上相关文档的召回率。存在并列时，应明确基线和评估规则。

### 8.3 把“没召回”和“排序不好”分开定位

如果精确检索本身找不到正确文档，要检查切片、模型、查询表述、输入前缀和度量契约；如果精确检索能找到，ANN 找不到，再检查索引参数、量化与过滤。

如果相关文档已经进入候选集，却排不到最终上下文里，则要检查融合、重排和候选截断。reranker 无法重新发现候选集合之外的文档。

过滤也要放进基线：先全局 Top-K 再过滤，可能让候选不足；直接对满足过滤条件的全集求 Top-K，则是另一个检索目标。比较精确与 ANN 时，必须使用一致的权限、租户、时间和元数据条件。

最终按一条完整链路评估：ANN 近邻 Recall@K、人工标注相关性的 Recall@K / MRR / nDCG、最终回答质量，以及延迟和成本。数值阈值应在自己的语料和查询分布上校准，“cosine 0.8”不是跨模型通用的相关性标准。

## 9. RRF 与 reranker：都改变排序，但依据不同

一个常见的混合检索流程是：

![RAG 检索链路：查询并行进入 BM25 和 embedding 向量 ANN 检索，两路排名经 RRF 融合去重后，候选文本进入 reranker，最终选取上下文供 LLM 生成。]({{ '/pics/vector-search-metrics/rag-retrieval-pipeline.svg' | relative_url }}){:height="60%" width="60%"}

*图 5：向量度量、ANN、RRF 和 reranker 位于不同环节。图中候选数量仅用于说明，不是推荐固定值。*

### 9.1 RRF 根据名次融合多路结果

BM25 的分数、cosine 的分数和其他检索器的分数通常不能直接相加。RRF（Reciprocal Rank Fusion，倒数排名融合）绕开分数尺度，使用文档在各路结果中的名次：

$$
\operatorname{RRF}(x)
=\sum_{i:\,x\in L_i}\frac{1}{k+r_i(x)}.
$$

$L_i$ 是第 $i$ 路返回列表，$r_i(x)$ 从 1 开始。文档不在某一路列表中，该路贡献 0；$k$ 是平滑常数，**不是 Top-K 的 K**。经典论文采用 $k=60$，这不是所有语料上的最优保证。参见 [Cormack 等人的 SIGIR 2009 论文](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)。

设两路结果为：

| 名次 | BM25 | Vector |
|---|---|---|
| 1 | A | C |
| 2 | B | A |
| 3 | C | D |

取 $k=60$：

| 文档 | RRF 计算 | 分数（约） |
|---|---|---|
| A | $1/61+1/62$ | 0.032522 |
| C | $1/63+1/61$ | 0.032266 |
| B | $1/62$ | 0.016129 |
| D | $1/63$ | 0.015873 |

最终是 A → C → B → D。A 在两路都比较靠前，所以超过了只在向量路排名第一、在 BM25 排名第三的 C。

$k$ 越小，前几名之间的分数差越明显；$k$ 较大时，同一列表内部的名次差被压平，同时出现在多路中的候选通常更容易获益。列表截断深度、检索路之间的相关性与重复路数也会影响融合，不能把重复的同一路结果当成独立证据无限叠加。

实现时先约定稳定的候选 ID，在每一路内部去重，再跨路合并。同一文档的不同 chunk 是否合并，是业务选择：如果提前把 chunk 粗暴合成文档，就可能丢掉真正回答问题的片段。RRF 不读取文本、不学习语义，也不把排名分数转换成相关概率。

### 9.2 Reranker 重新评估查询与候选的相关性

典型的 cross-encoder reranker 把查询和候选文本一起输入模型，输出新的相关性分数。它能利用两者之间的细粒度交互，例如查询中的版本、否定词或条件限制；但实际收益必须在自己的数据上验证。

与先分别编码再计算相似度的 bi-encoder 相比，这种联合计算通常更贵，因此一般用于有限候选集。参见 [Sentence Transformers 的 Retrieve & Re-Rank 文档](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)。

reranker 是一类重排方法，不只包含 cross-encoder，也可以使用 late interaction、学习排序或 LLM。它的输出不一定在 $[0,1]$；有些模型返回 logits，即使套用 sigmoid，也不自动成为经过校准的正确概率。参见 [Cross-Encoder 模型说明](https://sbert.net/docs/cross_encoder/pretrained_models.html)。

| 对比项 | RRF | 典型 cross-encoder reranker |
|---|---|---|
| 输入 | 多路候选 ID 与名次 | 查询文本与候选文本 |
| 判断依据 | 在各路排名中的位置 | 模型对文本相关性的判断 |
| 是否需要额外模型推理 | 否 | 是 |
| 主要作用 | 融合互补的检索结果 | 对已召回候选做更细的相关性排序 |
| 主要成本 | 列表处理与排序 | 模型推理、文本长度与候选数量 |
| 能否找回所有路都未返回的文档 | 不能 | 不能 |

两者可以串联：先扩大召回覆盖，用 RRF 融合，再截取候选送入 reranker。也可以根据质量和延迟要求采用其他组合。它们不是互相替代的固定选项，更不能把 RRF 理解成另一种向量距离。

## 10. 配置检索时，我会先核对这些问题

1. **模型契约**：查询和文档的编码方式、版本、维度和推荐度量是否匹配？
2. **归一化条件**：哪些向量已经单位化？是在入库前、查询时，还是由数据库处理？零向量怎么处置？
3. **分数语义**：返回的是距离、平方距离、相似度，还是包装层变换后的分数？阈值方向是否正确？
4. **近似误差**：精确检索能否召回？ANN 在相同过滤条件下的 Recall@K 和延迟是多少？
5. **融合与重排**：多路排名是否正确去重？候选预算是否足够？reranker 是否适合中文、当前领域和文本长度？

L2、cosine 和 IP 的关系可以由公式准确推导。真正把它们用于 RAG 时，还需要让模型、预处理、索引、过滤、融合和重排共享一致的检索目标，再用评估数据判断每一层是否达到了这个目标。
