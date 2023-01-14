---
layout:     post
title:      "实现 K8s client-go 的监控"
date:       2020-07-18 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

这里的client-go的监控是指，对client-go的请求时延、QPS等的监控，任何第三方控制器可以使用client-go来向K8s apiserver发送请求，并实现对client-go的监控。本文以k8s 1.14为例，介绍如何实现client-go的监控。
##### client-go监控接口
client-go提供的监控接口定义在`client-go/tools/metrics/metrics.go`，在这个文件中，定义了两个接口，一个用来采集时延数据，另一个用于采集状态码信息。因此，用户可以自定义采集方法，并注册这里。
```go
// 用来将用户定义的监控采集实现注册到这里的全局变量，限制为只注册一次
var registerMetrics sync.Once

// LatencyMetric observes client latency partitioned by verb and url.
type LatencyMetric interface {
    // verb是指：PUT/DELETE/GET等
	Observe(verb string, u url.URL, latency time.Duration)
}

// ResultMetric counts response codes partitioned by method and host.
type ResultMetric interface {
    // 这里的method也是verb
	Increment(code string, method string, host string)
}

var (
    //组件内置的全局变量，用来接收用户的注册，组件会调用这些变量去采集数据。
    //如果用户不注册，这里的noop实现不会做任何事情
	// RequestLatency is the latency metric that rest clients will update.
	RequestLatency LatencyMetric = noopLatency{}
	// RequestResult is the result metric that rest clients will update.
	RequestResult ResultMetric = noopResult{}
)

// 注册监控实现，只能被注册两次
func Register(lm LatencyMetric, rm ResultMetric) {
	registerMetrics.Do(func() {
		RequestLatency = lm
		RequestResult = rm
	})
}
```
我们先看一下，client-go是如何使用这两个全局变量的。以`RequestLatency`为例。这个全局变量只有一个地方用到了`k8s.io/client-go/rest/request.go`：
```go
// request connects to the server and invokes the provided function when a server response is
// received. It handles retry behavior and up front validation of requests. It will invoke
// fn at most once. It will return an error if a problem occurred prior to connecting to the
// server - the provided function is responsible for handling server errors.
func (r *Request) request(fn func(*http.Request, *http.Response)) error {
	//Metrics for total request latency
	start := time.Now()
	defer func() {
		metrics.RequestLatency.Observe(r.verb, r.finalURLTemplate(), time.Since(start))
    }()
    
    // 省去其他代码
    // ...
```
由此可见，这个方法就是在向apiserver发送请求之前记录一个时间，等请求返回后统计一下时延。这里是数据的采集，那么采集的数据交给谁消费呢？这就是需要用户定义Metrics的实现了，或者交给prometheus，或者交给自定义的数据消费系统。

同时用户在提供接口供client-go调用的时候，可能还需要关注一些除了`verb`，`url`之外的一些信息，这些信息都可以打成标签，用来分类，这些都可以在接口中去做。

以prometheus为例，可以用下面方式把数据交给prometheus，至此，client-go的监控就算是完成了。
```go
var (
	requestLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "name",
			Help:    "help",
			Buckets: prometheus.ExponentialBuckets(0.1, 1, 10),
		},
		[]string{"labelName", "verb", "url"},
	)
)

func init() {
	prometheus.MustRegister(requestLatency)
	metrics.Register(&metricsAdapter{requestLatency}, &resultAdapter{})
}

type metricsAdapter struct {
	m *prometheus.HistogramVec
}

func (l *metricsAdapter) Observe(verb string, u url.URL, latency time.Duration) {
	l.m.WithLabelValues("labelName", verb, u.String()).Observe(latency.Seconds())
}
```

除了client-go，其他组件也都提供了数据采集的接口，用户可以自定义接口来收集数据，比如：leaderelection, reflector, workqueue等，这些组件的监控是不会影响业务实现的，设计的挺好。

##### 自定义组件的监控
对于自定义组件的监控，我们也可以采用系统组件的设计方式，提供全局变量用来接收注册，但是数据采集需要由我们自己来做了，数据的消费者和生产者通过这些接口来通信，被绑定在一起。