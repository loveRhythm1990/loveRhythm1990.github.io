---
layout:     post
title:      "Golang 调度器[转载]"
date:       2020-03-15 8:42:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

> 本文是对 *tonybai* 老师的《[也谈goroutine调度器](https://tonybai.com/2017/06/23/an-intro-about-goroutine-scheduler/)》的理解

#### G-P-M模型
在Go 1.0版本中，Go team实现了一个简单的调度器。在这个调度器中，goroutine被抽象为G，而os thread作为"物理CPU"被抽象为M，这个模型重要不足：限制了Go并发程序的伸缩性，尤其是对那些有高吞吐或并行计算需求高的服务程序，主要体现在如下几个方面：
* 单一全局互斥锁（Sched.Lock）和集中状态存储的存在导致所有goroutine相关操作，比如：创建、重新调度等都要上锁；
* goroutine传递问题：M经常在M之间传递"可运行"的goroutine，这导致调度延迟增大以及额外的性能损耗；
* 每个M做内存缓存，导致内存占用过高，数据局部性较差；
* 由于syscall调用而形成的剧烈的worker thread阻塞和解除阻塞，导致额外的性能损耗。

> 补充，goroutine占用资源非常少，Go 1.4将每个goroutine stack的size设置为2k，（Go 1.2 goroutine stack has been increased from 4Kb to 8Kb），goroutine调度的切换也不用陷入到操作系统内核层完成，代价很低。

基于以上问题，诞生了G-P-M模型，示意图如下：
![java-javascript](/img/in-post/go-scheduler/goroutine-scheduler-model.png){:height="60%" width="60%"}

在上图中P是一个"逻辑Processor"，每个G想要运行起来，首先需要被分配一个P（进入到P的local runq中），对于G来说，P就是运行它的"CPU"，可以说：G的眼里只有P。但从Go scheduler视角来看，真正的"CPU"是M，只有将P和M绑定才能让P的runq中G得以真实运行起来。这样的P与M的关系，就好比Linux操作系统调度层面用户线程(user thread)与核心线程(kernel thread)的对应关系那样(N x M)。

> 使用runtime.GOMAXPROCS(n int)可以设置使用的M的个数，这个函数调用会STW，另外默认的P的大小应该是逻辑处理器的个数（待查证）。

#### 抢占式调度
Go 1.2中实现了"抢占式"调度，[《Go Preemptive Scheduler Design》](https://docs.google.com/document/d/1ETuA2IOmnaQ4j81AtTGT40Y4_Jr6_IDASEKg0t0dBR8/edit#!)，抢占式调度的原理是在每个函数或者方法的入口，加上一段额外的代码，让runtime有机会检查是否需要执行抢占调度。这种解决方案只能说局部解决了“饿死”问题，对于没有函数调用，纯算法循环计算的G，scheduler依然无法抢占（这里说的是这个G在执行一个无限循环，而且在循环中不会调用其他方法，那调度器就无法抢占这个goroutine了）。

#### 其他优化
Go runtime已经实现了[netpoller](http://morsmachine.dk/netpoller)，这使得即便发起网络I/O操作也不会导致M被阻塞（仅阻塞G），从而不会导致大量M被创建出来。但是对于regular file的I/O操作一旦被阻塞，那么M将会进入Sleep状态，等待IO返回后被唤醒；这种情况下P将与sleep的M分离，再选择一个idle的M。如果此时没有idle的M，就会创建一个M，这就是为何大量IO操作导致大量Thread被创建出来的原因。

#### 进一步理解
##### G-P-M
GPM的定义在`src/runtime/runtime2.go`这个源文件，这三个struct都比较大，从代码看GPM的大致用途如下：
* G: goroutine，存储了goroutine的执行stack信息，goroutine的状态以及goroutine的任务函数等；另外G对象是可以重用的。
* P: 逻辑processor，P的数量决定了系统内最大可并行的G的数量（前提：系统的物理cpu核数>=P的数量）；P的最大作用还是拥有的各种G对象队列、链表、一些cache和状态。
* M: M代表着真正的执行计算资源。在绑定有效的p后，进入scheduler循环；而schedule循环的机制大致是从各种队列、p的本地队列中获取G，切换到G的执行栈上并执行G的函数，调用goexit做清理工作并回到m，如此反复。M并不保留G的状态，这是G可以跨M调度的基础。

关于work-stealing算法：1)每个ｐ维护一个Ｇ队列;2)当一个Ｇ被创建出来，或者变为可执行状态时，就把他放到Ｐ的可执行队列中；3)当一个Ｇ执行结束时，ｐ会从队列中把该Ｇ取出；如果此时Ｐ队列为空，即没有其他Ｇ可以执行，就随机选择另一个Ｐ，从其可执行的Ｇ队列中偷取一半。**该算法避免了在goroutine调度时使用全局锁**，（steal之前会先查看全局的可运行队列是否有goroutine）

Go运行时会在下面的goroutine被阻塞的情况下运行另外一个goroutine，（很明显使用关键字go可以导致新goroutine的调度）
* blocking syscall (for example opening a file)，系统调用
* network input，网络输入
* channel operations，channel 操作
* primitives in the sync package，同步操作

##### 抢占G
和操作系统按时间片调度线程不同，Go并没有时间片的概念。如果某个G没有进行system call调用、没有进行I/O操作、没有阻塞在一个channel操作上，那么m是如何让G停下来并调度下一个runnable G的呢？答案是：G是被抢占调度的。

前面说过，除非极端的无限循环或死循环，否则只要G调用函数，Go runtime就有抢占G的机会。Go程序启动时，runtime会去启动一个名为sysmon的m(一般称为监控线程)，该m无需绑定p即可运行，该m在整个Go程序的运行过程中至关重要：
```go
//$GOROOT/src/runtime/proc.go

// The main goroutine.
func main() {
     ... ...
    systemstack(func() {
        newm(sysmon, nil)
    })
    .... ...
}

// Always runs without a P, so write barriers are not allowed.
//
//go:nowritebarrierrec
func sysmon() {
    // If a heap span goes unused for 5 minutes after a garbage collection,
    // we hand it back to the operating system.
    scavengelimit := int64(5 * 60 * 1e9)
    ... ...

    if  .... {
        ... ...
        // retake P's blocked in syscalls
        // and preempt long running G's
        if retake(now) != 0 {
            idle = 0
        } else {
            idle++
        }
       ... ...
    }
}
```
sysmon每20us~10ms启动一次，按照《Go语言学习笔记》中的总结，sysmon主要完成如下工作：
* 释放闲置超过5分钟的span物理内存；
* 如果超过2分钟没有垃圾回收，强制执行；
* 将长时间未处理的netpoll结果添加到任务队列；
* 向长时间运行的G任务发出抢占调度；
* 收回因syscall长时间阻塞的P；

我们看到sysmon将“向长时间运行的G任务发出抢占调度”，（从这里看，如果一个goroutine连续运行10ms，就认为需要抢占了），这个事情由retake实施：
```go
// forcePreemptNS is the time slice given to a G before it is
// preempted.
const forcePreemptNS = 10 * 1000 * 1000 // 10ms

func retake(now int64) uint32 {
          ... ...
           // Preempt G if it's running for too long.
            t := int64(_p_.schedtick)
            if int64(pd.schedtick) != t {
                pd.schedtick = uint32(t)
                pd.schedwhen = now
                continue
            }
            if pd.schedwhen+forcePreemptNS > now {
                continue
            }
            preemptone(_p_)
         ... ...
}
```
可以看出，如果一个G任务运行10ms，sysmon就会认为其运行时间太久而发出抢占式调度的请求。一旦G的抢占标志位被设为true，那么待这个G下一次调用函数或方法时，runtime便可以将G抢占，并移出运行状态，放入P的local runq中，等待下一次被调度。

##### channel阻塞或network I/O情况下的调度
如果G被阻塞在某个channel操作或network I/O操作上时，**G会被放置到某个wait队列中，而M会尝试运行下一个runnable的G**；如果此时没有runnable的G供m运行，那么m将解绑P，并进入sleep状态。当I/O available或channel操作完成，在wait队列中的G会被唤醒，标记为runnable，放入到某P的队列中，绑定一个M继续执行

##### system call阻塞情况下的调度
如果G被阻塞在某个systemcall操作上，那么不光G会阻塞，执行该G的M也会被解绑P（实质是被sysmon抢走了），与G一起进入Sleep状态。如果此时有idle的M，则P与其绑定继续执行其他G；如果没有idle的M，但仍然有其他G要去执行，那么就会创建一个新M。

当阻塞在syscall上的G完成syscall调用后，G会去尝试获取一个可用的P，如果没有可用的P，那么G会被标记为runnable，之前的那个sleep的M将再次进入sleep。

####　调度器状态的查看方法
Go提供了调度器当前状态的查看方法：使用Go运行时环境变量GODEBUG。
```s
$GODEBUG=schedtrace=1000 godoc -http=:6060
SCHED 0ms: gomaxprocs=4 idleprocs=3 threads=3 spinningthreads=0 idlethreads=0 runqueue=0 [0 0 0 0]
SCHED 1001ms: gomaxprocs=4 idleprocs=0 threads=9 spinningthreads=0 idlethreads=3 runqueue=2 [8 14 5 2]
SCHED 2006ms: gomaxprocs=4 idleprocs=0 threads=25 spinningthreads=0 idlethreads=19 runqueue=12 [0 0 4 0]
SCHED 3006ms: gomaxprocs=4 idleprocs=0 threads=26 spinningthreads=0 idlethreads=8 runqueue=2 [0 1 1 0]
SCHED 4010ms: gomaxprocs=4 idleprocs=0 threads=26 spinningthreads=0 idlethreads=20 runqueue=12 [6 3 1 0]
SCHED 5010ms: gomaxprocs=4 idleprocs=0 threads=26 spinningthreads=1 idlethreads=20 runqueue=17 [0 0 0 0]
SCHED 6016ms: gomaxprocs=4 idleprocs=0 threads=26 spinningthreads=0 idlethreads=20 runqueue=1 [3 4 0 10]
... ...
```
GODEBUG这个Go运行时环境变量很是强大，通过给其传入不同的key1=value1,key2=value2… 组合，Go的runtime会输出不同的调试信息，比如在这里我们给GODEBUG传入了”schedtrace=1000″，其含义就是每1000ms，打印输出一次goroutine scheduler的状态，每次一行。每一行各字段含义如下(以上面例子中最后一行为例)：
```s
SCHED 6016ms: gomaxprocs=4 idleprocs=0 threads=26 spinningthreads=0 idlethreads=20 runqueue=1 [3 4 0 10]

SCHED：调试信息输出标志字符串，代表本行是goroutine scheduler的输出；
6016ms：即从程序启动到输出这行日志的时间；
gomaxprocs: P的数量；
idleprocs: 处于idle状态的P的数量；通过gomaxprocs和idleprocs的差值，我们就可知道执行go代码的P的数量；
threads: os threads的数量，包含scheduler使用的m数量，加上runtime自用的类似sysmon这样的thread的数量；
spinningthreads: 处于自旋状态的os thread数量；
idlethread: 处于idle状态的os thread的数量；
runqueue=1： go scheduler全局队列中G的数量；
[3 4 0 10]: 分别为4个P的local queue中的G的数量。
```
我们还可以输出每个goroutine、m和p的详细调度信息，但对于Go user来说，绝大多数时间这是不必要的：
```s
$ GODEBUG=schedtrace=1000,scheddetail=1 godoc -http=:6060

SCHED 0ms: gomaxprocs=4 idleprocs=3 threads=3 spinningthreads=0 idlethreads=0 runqueue=0 gcwaiting=0 nmidlelocked=0 stopwait=0 sysmonwait=0
  P0: status=1 schedtick=0 syscalltick=0 m=0 runqsize=0 gfreecnt=0
  P1: status=0 schedtick=0 syscalltick=0 m=-1 runqsize=0 gfreecnt=0
  P2: status=0 schedtick=0 syscalltick=0 m=-1 runqsize=0 gfreecnt=0
  P3: status=0 schedtick=0 syscalltick=0 m=-1 runqsize=0 gfreecnt=0
  M2: p=-1 curg=-1 mallocing=0 throwing=0 preemptoff= locks=1 dying=0 helpgc=0 spinning=false blocked=false lockedg=-1
  M1: p=-1 curg=17 mallocing=0 throwing=0 preemptoff= locks=0 dying=0 helpgc=0 spinning=false blocked=false lockedg=17
  M0: p=0 curg=1 mallocing=0 throwing=0 preemptoff= locks=1 dying=0 helpgc=0 spinning=false blocked=false lockedg=1
  G1: status=8() m=0 lockedm=0
  G17: status=3() m=1 lockedm=1

SCHED 1002ms: gomaxprocs=4 idleprocs=0 threads=13 spinningthreads=0 idlethreads=7 runqueue=6 gcwaiting=0 nmidlelocked=0 stopwait=0 sysmonwait=0

 P0: status=2 schedtick=2293 syscalltick=18928 m=-1 runqsize=12 gfreecnt=2
  P1: status=1 schedtick=2356 syscalltick=19060 m=11 runqsize=11 gfreecnt=0
  P2: status=2 schedtick=2482 syscalltick=18316 m=-1 runqsize=37 gfreecnt=1
  P3: status=2 schedtick=2816 syscalltick=18907 m=-1 runqsize=2 gfreecnt=4
  M12: p=-1 curg=-1 mallocing=0 throwing=0 preemptoff= locks=0 dying=0 helpgc=0 spinning=false blocked=true lockedg=-1
  M11: p=1 curg=6160 mallocing=0 throwing=0 preemptoff= locks=2 dying=0 helpgc=0 spinning=false blocked=false lockedg=-1
  M10: p=-1 curg=-1 mallocing=0 throwing=0 preemptoff= locks=0 dying=0 helpgc=0 spinning=false blocked=true lockedg=-1
 ... ...

SCHED 2002ms: gomaxprocs=4 idleprocs=0 threads=23 spinningthreads=0 idlethreads=5 runqueue=4 gcwaiting=0 nmidlelocked=0 stopwait=0 sysmonwait=0
  P0: status=0 schedtick=2972 syscalltick=29458 m=-1 runqsize=0 gfreecnt=6
  P1: status=2 schedtick=2964 syscalltick=33464 m=-1 runqsize=0 gfreecnt=39
  P2: status=1 schedtick=3415 syscalltick=33283 m=18 runqsize=0 gfreecnt=12
  P3: status=2 schedtick=3736 syscalltick=33701 m=-1 runqsize=1 gfreecnt=6
  M22: p=-1 curg=-1 mallocing=0 throwing=0 preemptoff= locks=0 dying=0 helpgc=0 spinning=false blocked=true lockedg=-1
  M21: p=-1 curg=-1 mallocing=0 throwing=0 preemptoff= locks=0 dying=0 helpgc=0 spinning=false blocked=true lockedg=-1
... ...
```
关于go scheduler调试信息输出的详细信息，可以参考Dmitry Vyukov的大作：[《Debugging performance issues in Go programs》](https://software.intel.com/en-us/blogs/2014/05/10/debugging-performance-issues-in-go-programs)。这也应该是每个gopher必读的经典文章。当然更详尽的代码可参考$GOROOT/src/runtime/proc.go中的schedtrace函数。

### 参考

[Debugging performance issues in Go programs](https://software.intel.com/en-us/blogs/2014/05/10/debugging-performance-issues-in-go-programs)

[go夜读 golang 中 goroutine 的调度](https://reading.developerlearning.cn/reading/12-2018-08-02-goroutine-gpm/)

[鸟窝 Go 调度器: M, P 和 G](https://colobu.com/2017/05/04/go-scheduler/)

[鸟窝 Golang调度器源码分析](https://colobu.com/2017/05/04/golang-runtime-scheduler/)

[深入golang runtime的调度](https://zboya.github.io/post/go_scheduler/)