---
layout:     post
title:      "Golang profile 性能分析与运行时参数概述"
date:       2023-11-09 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Golang
---

**目录**
- [通过 pprof 与 trace 进行性能分析](#通过-pprof-与-trace-进行性能分析)
  - [profile 采集与分析](#profile-采集与分析)
  - [trace 采集与分析](#trace-采集与分析)
- [GOMEMLIMIT 环境变量](#gomemlimit-环境变量)
- [automaxprocs 自动配置 GOMAXPROCS](#automaxprocs-自动配置-gomaxprocs)
- [参考](#参考)

## 通过 pprof 与 trace 进行性能分析
官方文档 [https://pkg.go.dev/net/http/pprof](https://pkg.go.dev/net/http/pprof)，以及官方一篇文章 [Profiling Go Programs](https://go.dev/blog/pprof)。
### profile 采集与分析
安装 graphviz，`brew install graphviz`，用于 web 展示。Go 收集 metrics 的指标命令如下。
```s
curl http://localhost:6060/debug/pprof/profile\?seconds\=30 -o cpu_profile
curl http://localhost:6060/debug/pprof/heap -o heap
curl http://localhost:6060/debug/pprof/goroutine\?debug\=1 -o goroutinedebug1
```
收集指标时，可添加 seconds 参数，此参数不同 profile 有不同的含义：1）对于 allocs, block, goroutine, heap, mutex, threadcreate 返回的是指定时间段的增量 profile（因为是 delta 所以有可能是负数，针对内存泄漏或者 goroutine 泄漏，这种方式更容易发现问题）；2）对于 cpu (profile), trace 是指采集固定的时间段。

> 参考 https://pkg.go.dev/net/http/pprof#hdr-Parameters：
> 
> // - debug=N (all profiles): response format: N = 0: binary (default), N > 0: plaintext
> 
> // - gc=N (heap profile): N > 0: run a garbage collection cycle before profiling
> 
> // - seconds=N (allocs, block, goroutine, heap, mutex, threadcreate profiles): return a delta profile
> 
> // - seconds=N (cpu (profile), trace profiles): profile for the given duration

在查看 goroutine 时，如果不加 debug 参数，返回的 binary 形式的结果，可以使用 go tool pprof 分析，如果是 `debug=1` 则是概述性的分析（如果 goroutine stack 相同则会合并），如果是 `debug=2` 会单独打印每个 goroutine 的 stack。

我们以 heap 为例，解释下 pprof 输出数据中每列的含义。下图是 heap profile 的 top 输出。其中：
* Flat, Flat%: 在采样周期中，正在运行的函数所消耗的资源（比如第一行是 Alloc 函数自身消耗了 1831kB），正在运行的函数消耗资源所占总资源的比例。这里的"正在运行"指的是本函数正在 Running，而不是本函数调用的子函数。可以区别 Cum，Cum 涵盖了本函数以及本函数调用的子函数。
* Sum%: 累积的百分比，是从第一行到当前行，所有 Flat% 的和。
* Cum, Cum%: 采样周期中，函数所消耗的资源，包括了两种情况：1）当前函数，2）当前函数正在调用的子函数。查看 top 时，有时候观察到 Flat 和 Flat% 都是 0，但是 Cum 不为 0，应该就是第二种 case。

![java-javascript](/pics/pprof-top.png) 


查看 profile 时，可以使用 `-http=:8081` 在本地启动一个 profile web 服务器，此时可以在浏览器页面查看 profile 信息，浏览器中的信息较为丰富，比如可以查看火焰图。
```s
go tool pprof -http=:8081 ./profile
go tool pprof -http=:8081 ./heap
go tool pprof -http=:8081 ./main ./goroutinedebug1
go tool pprof -http=:8081 http://localhost:6060/debug/pprof/goroutine -o goroutines
```
![java-javascript](/pics/pprof_flame.png) 

在 K8s 集群中，如果我们将 pod 的端口 forward 到了本地，使用 `kubectl port-forward pod/podname -n namespace` 命令，也可以直接使用浏览器查看 pod 的 profile（通过 url: `http://localhost:8082/debug/pprof/goroutine?debug=2`），直接在浏览器查看时，不能将 debug 参数设置为 0，goroutine 比较多时，可以通过关键词 `minutes` 或者应用程序中的关键字进行搜索。

采集 block 和 mutex 的命令如下，这两个 profile 只有在代码中添加了代码[runtime.SetBlockProfileRate](https://pkg.go.dev/runtime#SetBlockProfileRate)和[runtime.SetMutexProfileFraction](https://pkg.go.dev/runtime#SetMutexProfileFraction)才起作用，否则采样是空的。
```s
go tool pprof http://localhost:6060/debug/pprof/block
go tool pprof http://localhost:6060/debug/pprof/mutex
```

> 在采样 profile 时，有时会遇到错误 `Could not enable CPU profiling: cpu profiling already in use`，这是因为有另一个程序已经在采样了，比如同时运行两个 `curl http://localhost:6060/debug/pprof/profile -o cpu_profile` 就会报这个错误。

### trace 采集与分析
trace 的介绍参考官方文档《[Go Execution Tracer](https://docs.google.com/document/u/1/d/1FP5apqzBgr7ahCCgFO-yoVhk4YZrNIDNf9RybngBc14/pub)》。
Go trace 对于分析程序运行时延非常有用。采集 trace 同样是引入 package `_ "net/http/pprof"` 即可。
```s
curl http://127.0.0.1:8080/debug/pprof/trace -o trace.out
```
运行 `go tool trace ./trace.out`命令之后，会打开一个 web 页面，页面中的 `Goroutine analysis` 链接可以分析各个 goroutine 阻塞在一些事件上的时间。

![java-javascript](/pics/gotrace.png) 

对于 trace 《[通过实例理解Go Execution Tracer](https://tonybai.com/2021/06/28/understand-go-execution-tracer-by-example/)》有比较清晰的说明。

## GOMEMLIMIT 环境变量
[GOMEMLIMIT](https://pkg.go.dev/runtime/debug#SetMemoryLimit) 环境变量设置 go 运行时内存使用上限，debug 包里面的 `runtime/debug.SetMemoryLimit` 可以动态的配置这个值。配置了这个值以后，在内存快达到此上限后会触发 GC，即使设置环境变量 `GOGC=off`，这个配置的默认值是 `math.MaxInt64`。通过配置这个内存上限，使内存达到上限时发生 GC，[能缓解程序 OOM 的问题](https://docs.pingcap.com/zh/tidb/stable/configure-memory-usage/#%E8%AE%BE%E7%BD%AE%E7%8E%AF%E5%A2%83%E5%8F%98%E9%87%8F-gomemlimit-%E7%BC%93%E8%A7%A3-oom-%E9%97%AE%E9%A2%98)。

[《等等， 怎么使用 SetMemoryLimit?》](https://colobu.com/2022/06/20/how-to-use-SetMemoryLimit/) 根据配置的 SetMemoryLimit 大小以及 GOGC 开关分为了四种情况：
* SetMemoryLimit 足够大，GOGC=off：因为 MemoryLimit 足够大，程序的内存使用很难达到这个上限，又把 GC 关掉了，所以这种情况不会触发 GC。
* SetMemoryLimit 不够大，GOGC=off：MemoryLimit 不够大，程序内存使用频繁达到这个上限，所以也会频繁发生 GC。
* SetMemoryLimit 足够大，GOGC=100：这种情况下，基本是正常的 GC 逻辑，运行时会在**新申请的内存跟活跃内存一样大时**，触发 GC（GOGC=100 配置的效果，其中活跃内存是上次 GC 之后未回收的内存）。因为 MemoryLimit 足够大，所以 GC 基本是通过 GOGC=100 触发的。
* SetMemoryLimit 不够大，GOGC=100：这种情况下，两个配置都可能会触发 GC。

通过环境变量 `GODEBUG=gctrace=1` 可以打印 GC 日志。

## automaxprocs 自动配置 GOMAXPROCS
在 K8s 环境下，我们会给 pod 设置 cpu limit，比如 1 核，在默认情况下，pod 中的进程通过 `/proc` 文件系统看到的是宿主机的资源情况，也就在通过 `runtime.GOMAXPROCS(maxProcs)` 设置 Go 调度器中的 P(逻辑 processor) 时，会设置成主机的核数。P 过大会带来频繁、不必要的上下文切换，因此会影响程序性能。

项目 [https://github.com/uber-go/automaxprocs](https://github.com/uber-go/automaxprocs) 会根据 pod 的 limit 自动配置 max procs。其工作原理就是读取 `/proc/self/cgroup` 以及 `/proc/self/mountinfo` 文件，找到当前 Pod 所在的 cgroup，主要是 cpu 子系统，并读取 cpu 的 quota 的配置（cpu 使用上限，包括`cpu.cfs_quota_us` 以及 `cpu.cfs_period_us`），并向下取整。

使用方式是直接在 main 文件中，引入 package 就可以了，会在 init 函数中自动配置。
```go
import _ "go.uber.org/automaxprocs"

func main() {
  // Your application logic here.
}
```

## 参考

一个不错的 go pprof 介绍 [https://github.com/DataDog/go-profiler-notes](https://github.com/DataDog/go-profiler-notes)

《[瞬间高并发，goroutine执行结束后的资源占用问题](https://mp.weixin.qq.com/s/iBo-j4990paKb3Pb7Xk-2w)》 提出了一个现象，就是流量洪峰过后，Go 的一些运行时内存并不会释放，比如 G 结构体，导致 cpu 和 内存并不会回落到洪峰前的水位。

[GOMEMLIMIT is a game changer for high-memory applications](https://weaviate.io/blog/gomemlimit-a-game-changer-for-high-memory-applications)

[A Guide to the Go Garbage Collector](https://tip.golang.org/doc/gc-guide)

[Go语言垃圾回收指南](https://taoshu.in/go/gc-guide.html)

[go trace 统计原理与使用](https://www.cnblogs.com/hobbybear/p/17252973.html)