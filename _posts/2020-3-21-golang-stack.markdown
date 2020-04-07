---
layout:     post
title:      "golang stack基础"
date:       2020-03-21 16:23:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

在Go 1.4中，Go语言的stack处理由之前的"segmented stack"改为了"continuous stack"，在这两种方式中，每个goroutine的栈空间都不是固定的，只不过按需增长的方式不同。

#### segmented stack
分段栈(segmented stacks)是Go语言最初用来处理栈的方案。当创建一个goroutine时，Go运行时会分配一段8K字节的内存用于栈供goroutine运行使用，我们让goroutine在这个栈上完成其任务处理。当我们用光这8K字节的栈空间后，问题随之而来。为了解决这个问题，每个go函数在函数入口处都会有一小段代码(called prologue)，这段代码会检查是否用光了已分配的栈空间，如果用光了，这段代码会调用morestack函数。

morestack函数会分配一段新内存用作栈空间，接下来它会将有关栈的各种数据信息写入栈底的一个struct中(下图中Stack info)，包括上一段栈的地址。有点我们拥有了一个新的栈段(stack segment)，我们将重启goroutine，从导致栈空间用光的那个函数（译注：下图中的Foobar）开始执行。这就是所谓的“栈分裂 (stack split)”。

![java-javascript](/img/in-post/gostack/Picture1.png)

在新栈的底部，我们插入了一个栈入口函数lessstack。我们不会调用该函数，设置这个函数就是用于我们从那个导致我们用光栈空间的函数(译 注：Foobar)返回时用的。当那个函数(译注：Foobar)返回时，我们回到lessstack（这个栈帧），lessstack会查找 stack底部的那个struct，并调整栈指针(stack pointer)，使得我们返回到前一段栈空间。这样做之后，我们就可以将这个新栈段(stack segment)释放掉，并继续执行我们的程序了。还有一个分段栈示意图如下，来自([Contiguous stacks in Go](https://agis.io/post/contiguous-stacks-golang/))：

![java-javascript](/img/in-post/gostack/segmented-stacks.png)

##### 分段栈的问题-hotsplit
分段栈在函数退出时会进行收缩，如果不停地"调用函数--退出函数--调用函数--退出函数"，有可能这个栈就不停地扩展-收缩，典型代码如下：
```go
func main() {
    for {
        big()
    }
}

func big() {
    var x [8180]byte
    // do something with x

    return
}
```
在这种情况下，不停地creating以及destroying segments会产生性能问题，因此，在go 1.4之后，改为使用连续栈了。

#### continuous stack

g模型结构体在`src/runtime/runtime2.go`中定义，这个g结构体有两个field:`stackguard0/stackguard1`用来实现栈的自动收缩。

当栈需要扩展时，连续栈的做法如下：
1. 创建一个新的，更大的stack
2. 将旧的stack上的内容，拷贝到新的stack上
3. 修改栈中的指针值（逃逸到heap的不用改），使其值为拷贝后的变量的新地址
4. 销毁旧的stack

创建新stack时，会调用`runtime.morestack_noctxt`方法，该方法最终调用了`newstack`(src/runtime/stack.go):
```go
func newstack() {
   [...]
   // Allocate a bigger segment and move the stack.
   oldsize := gp.stack.hi - gp.stack.lo
   newsize := oldsize * 2
   if newsize > maxstacksize {
       print("runtime: goroutine stack exceeds ", maxstacksize, "-byte limit\n")
      throw("stack overflow")
   }

   // The goroutine must be executing in order to call newstack,
   // so it must be Grunning (or Gscanrunning).
   casgstatus(gp, _Grunning, _Gcopystack)

   // The concurrent GC will not scan the stack while we are doing the copy since
   // the gp is in a Gcopystack status.
   copystack(gp, newsize, true)
   if stackDebug >= 1 {
      print("stack grow done\n")
   }
   casgstatus(gp, _Gcopystack, _Grunning)
}
```
从代码看，基本是变为原来的两倍。连续栈的示意图如下：
![java-javascript](/img/in-post/gostack/2.png)


#### 参考文献

[Go语言是如何处理栈的](https://tonybai.com/2014/11/05/how-stacks-are-handled-in-go/)

[Go: How Does the Goroutine Stack Size Evolve?](https://medium.com/a-journey-with-go/go-how-does-the-goroutine-stack-size-evolve-447fc02085e5)

[How Stacks are Handled in Go](https://blog.cloudflare.com/how-stacks-are-handled-in-go/)

[Contiguous stacks in Go](https://agis.io/post/contiguous-stacks-golang/)
