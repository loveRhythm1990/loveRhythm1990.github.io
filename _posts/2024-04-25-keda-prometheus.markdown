---
layout:     post
title:      "配置 keda 使用 prometheus 数据源"
date:       2024-04-25 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s生态
---

**目录**
- [hpa 支持 prometheus 指标](#hpa-支持-prometheus-指标)
- [keda 支持 prometheus 指标](#keda-支持-prometheus-指标)
  - [scaleObject 配置](#scaleobject-配置)
  - [trigger 中的 metricType](#trigger-中的-metrictype)
    - [AverageValue](#averagevalue)
    - [Value](#value)
    - [Utilization](#utilization)
  - [prometheus scaler 实现](#prometheus-scaler-实现)

### hpa 支持 prometheus 指标
原生 hpa 支持 prometheus 时，需要部署 [prometheus-adapter](https://github.com/kubernetes-sigs/prometheus-adapter/blob/master/docs/walkthrough.md) 组件，部署该组件后，可以通过 K8s apiservice 访问 prometheus 指标，其中 apiservice 定义如下。
```yaml
apiVersion: apiregistration.k8s.io/v1
kind: APIService
metadata:
  name: v1beta2.custom.metrics.k8s.io
spec:
  group: custom.metrics.k8s.io
  groupPriorityMinimum: 100
  insecureSkipTLSVerify: true
  service:
    name: prometheus-adapter
    namespace: monitoring
  version: v1beta2
  versionPriority: 100
```
因此我们可以通过下面路径来访问该服务，并且 apiserver 会将服务重定向到服务 `monitoring/prometheus-adapter`。其中 custom.metrics.k8s.io 是 group，v1beta2 是 version。
```s
$ kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta2/namespaces/
                     default/pods/*/http_requests?selector=app%3Dsample-app"
```
在定义 hpa 指标时，需要指定类型为 Pods，即自定义指标类型。下面使用的指标名称为 `http_requests`，这是根据 prometheus-adapter 的配置得来的，具体需要参考 [Configuration Walkthroughs](https://github.com/kubernetes-sigs/prometheus-adapter/blob/master/docs/config-walkthrough.md)。总体来说使用原生 hpa 支持 prometheus 时，需要部署 adapter，还有写一些配置，比较复杂。
```yaml
  metrics:
  # use a "Pods" metric, which takes the average of the
  # given metric across all pods controlled by the autoscaling target
  - type: Pods
    pods:
      # use the metric that you used above: pods/http_requests
      metric:
        name: http_requests
      # target 500 milli-requests per second,
      # which is 1 request every two seconds
      target:
        type: Value
        averageValue: 500m
```

### keda 支持 prometheus 指标
#### scaleObject 配置
prometheus 是 keda 众多数据源中的一种，文档地址为 [prometheus-scaler](https://keda.sh/docs/2.15/scalers/prometheus/)，总体配置比较简单。
```yaml
triggers:
- type: prometheus
  metadata:
    # Required fields:
    serverAddress: http://<prometheus-host>:9090
    # Note: query must return a vector/scalar single element response
    query: sum(rate(http_requests_total{deployment="my-deployment"}[2m])) 
    threshold: '100.50'
    activationThreshold: '5.5'
    # Optional fields:
    # for namespaced queries, eg. Thanos
    namespace: example-namespace  
    # Optional. Custom headers to include in query. 
    # In case of auth header, use the custom authentication or 
    customHeaders: X-Client-Id=cid,X-Tenant-Id=tid,X-Organization-Id=oid relevant authModes.
    # Default is `true`, which means ignoring the empty value list from Prometheus. 
    # Set to `false` the scaler will return error when Prometheus target is lost
    ignoreNullValues: false 
    queryParameters: key-1=value-1,key-2=value-2
    #  Default is `false`, Used for skipping certificate check 
    # when having self-signed certs for Prometheus endpoint 
    unsafeSsl: "false"    
```
看几个重点的参数：
* serverAddress: prometheus 服务器地址，在 K8s 集群内部填 service 地址就可以了。
* query: prometheus 查询指标。用在这里的 PromQL 的结果必须是一个值：1）要么是一个 scalar类型；2）如果是 vector 类型，则 vector 类型必须只返回一个元素，否则 keda 不知道怎么处理数据。
* activationThreshold：激活阈值，是指 keda 从 0 扩到 1 的阈值，从 0 到 1 这个过程是 keda 负责的，原生 hpa 不支持缩容到 0。

这里需要注意的是 PromQL 表达式的写法，因为要求结果只有一个值，所以需要使用聚合函数 sum 对结果进行聚合。对于上面的例子来说，rate 需要一个 range vector `http_requests_total{deployment="my-deployment"}[2m]`，并返回一个 instant vector，因为 instant vector 的结果是一个数组，每个数组元素都是同一个指标 http_requests_total 但是具有不同的 label，所以需要使用聚合函数 sum 对结果进行聚合，sum 后面没有跟跟 by，那就是将所有的结果都聚合为一个值。

另外 **聚合函数只对 instant vector 起作用，并且其输出也是 instant vector**，因为 sum 没有跟 by 运算符进行分组，所以 instant vector 的结果是只有一个值。

#### trigger 中的 metricType
keda scaleobject 中可以指定 metricType，metricType 影响结果的计算，主要有三种类型： AverageValue, Value, Utilization。 默认是 AverageValue，具体可以参考文档 [keda-triggers](https://keda.sh/docs/2.15/reference/scaledobject-spec/#triggers)。

##### AverageValue
在 AverageValue 类型中， threshold 指定的是**每个副本的期望值**，因此在计算最终副本数的时候，计算过程如下：
1. metricValue/threshold，得到期望副本数。
2. 拿期望副本数与当前副本数做比较，决定扩容还是缩容。

从上面计算过程可以看出，metricValue 往往是一个 sum 值。

##### Value
Value 类型并不关注每个副本的平均值，其 threshold 配置的是一个绝对值，比如 kafka 中消息队列的平均等待时延，假设配置的是 5ms，但是目前是但是当前是 20ms，那么期望副本数为：20/5*(当前副本数)。

也能看出 Value 的 metricValue 往往是一个平均值。

##### Utilization
Utilization 多用于 CPU、内存等资源利用率的监控，通过设定一个目标利用率来保证资源的高效使用。例如设置 CPU 利用率的 Utilization=70%，当每个 Pod 的 CPU 使用率平均达到 70% 时，keda 会触发扩容。

#### prometheus scaler 实现
这部分内容参考 keda 的源代码 [prometheus_scaler.go](https://github.com/kedacore/keda/blob/main/pkg/scalers/prometheus_scaler.go)。keda 在处理 prometheus 指标类型时，是直接向 prometheus server 发送请求并结果反序列化为 promQueryResult 结构体类型。
```go
func (s *prometheusScaler) ExecutePromQuery(ctx context.Context) (float64, error) {

	// 1. 拼 url
	queryEscaped := url_pkg.QueryEscape(s.metadata.Query)
	url := fmt.Sprintf("%s/api/v1/query?query=%s&time=%s", s.metadata.ServerAddress, queryEscaped, t)


	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return -1, err
	}

	// 2. 反序列化指标
	r, err := s.httpClient.Do(req)
	b, err := io.ReadAll(r.Body)
	var result promQueryResult
	err = json.Unmarshal(b, &result)
	if err != nil {
		return -1, err
	}
	return v, nil
}

// prometheus 返回的指标数据结构
type promQueryResult struct {
	Status string `json:"status"`

	Data struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Metric struct{}      `json:"metric"`
			Value  []interface{} `json:"value"`
		} `json:"result"`
	} `json:"data"`
}
```