---
layout:     post
title:      "golang memory"
date:       2020-2-9 18:51:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

这部分内容是对一篇英文博客的理解，对涉及到的一些知识点进行了拓展，原文地址为：

[GO MEMORY MANAGEMENT](https://povilasv.me/go-memory-management/#)

### 理解VSZ与RSS
以下面为例，VSZ以及RSS的单位都是`KB`：
```bash
root@z-Latitude:~# ps -u --pid 4435
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root      4435  0.0  0.0 308680  5200 pts/0    Sl   18:27   0:00 /tmp/___go_build_mem_go
```
首先是`VSZ`，全称是`Virtual Memory Size`，包括进程能访问的所有内存，也包括被swap到硬盘上的内存，包括已经被分配但是没有被使用的内存，也包括加载了动态链接库（shared libraries）的内存。

其次是`RSS`，全称是`Resident Set Size`，包括在内存中的，进程已经分配的内存，不包括swap到硬盘上的内存，也包括加载的动态链接库的内存，包括进程分配的堆内存以及栈内存。因为RSS包括了动态链接库的内存，所以所有进程的RSS值加起来会超过系统的内存总值。同时，已经分配但是没有使用的内存，可能并不在RSS中，等到这部分内存被使用时才统计。因此如果你申请了一大块内存，并且逐步使用的时候，会发现RSS值在一直上升，但是VSZ值不变。

动态链接库是不包含在进程的可执行文件（ELF文件）中的，ELF文件中仅包括这个动态链接库的名字，在运行期间会寻找动态库并加载它。默认情况下，系统在/lib和/usr/lib文件夹下寻找动态库，找不到就报错，也可以设置环境变量`LD_LIBRARY_PATH`指定目录。

### 内存分配器
golang使用的内存分配器是[TCMalloc:Thread-Caching Malloc](http://goog-perftools.sourceforge.net/doc/tcmalloc.html)，但并不是完全照搬过来了，做了一些改动，这一小部分介绍TCMalloc工作的大致原理。首先是TCMalloc的特点：

* TCMalloc比glibc2.3 malloc快
* TCMalloc减少了多线程申请内存时的锁竞争，小对象几乎没有锁竞争，For large objects, TCMalloc tries to use fine grained and efficient spinlocks

TCMalloc的核心是为每个thread添加thread-local缓存，那么小对象就直接从这部分缓存中申请，这时候是没有锁竞争的，如果，thread-local缓存不够用了，那就从central缓存申请，大概是下面这个样子。
![java-javascript](/img/in-post/golang-mem/tcmalloc_lib.png)

TCMalloc中，小对象（size <= 32K）的分配方式跟大对象是不同的，大对象由页级别（page-level allocator）的分配器直接从central heap中分配。小对象会被round up到大概170个不同的大小等级。
![java-javascript](/img/in-post/golang-mem/tcmalloc_lib2.png)

#### 小对象(size<32K)的分配步骤：
* 将小对象round up到相应的大小等级，此时假设大小为size。
* 在当前thread的local cache中查找对应大小的size list，
* 如果size list不为空，也就是含有空闲的对应大小的空间，就分配
* 如果size list为空，从central list中获取一些空间，放到这个size list中，供刚刚的申请使用。central list是所有thread共享的。
* 如果central list为空，使用central page allocator分配一些空间，将空间按照size分割，将分配的空间放到central list，并分配给thread local list。

#### 大对象（size>32K)的分配过程：
当对象大于32K时，首先round up成4K（一个page的大小）的倍数，然后从central heap中分配空间，central heap也是一个链表数组，结构如下：
![java-javascript](/img/in-post/golang-mem/tcmalloc_lib3.png)
第k个表项中，每个表项的节点含有的空间大小是k*4K，第256个表项是用来分配空间大于256page的。

分配大小为k*4K的对象过程为：
* 在第k个链表中查找，有空间就使用。
* 没有空间就查找下一个链表（跟小对象好像不太一样）。
* 如果所有链表都查找结束，仍然没有空间，那就需要向linux系统申请了。
* 如果在第j个链表中分配的，其中j大于k，那么j的一个节点还是还是有空闲空间的，那么这个空闲空间要分出来重新插入到page heap的对应大小的链表中。

### Go内存分配
go语言的内存分配器跟TCMalloc类似，也是依靠spans（一些pages）来管理内存，使用thread-local内存，并依据大小来划分内存。**Spans**一块连续的内初区域（大于等于8K），关于span的代码描述，在golang代码文件`runtime/mheap.go`中，有一个mspan的结构体，有三类span:
* **idle**-span，不含有object，可以返回给OS，或者在堆分配时重新利用，或在栈分配时重新利用。
* **in use**-span，至少含有一个object，也许还有空闲空间。
* **stack**-span，用来分配goroutine stack空间的。This span can live either in stack or in heap, but not in both.

当申请空间时，object首先被分为三种大的类别：**Tiny** class for object <16 bytes，**Small** class for objects up to 32 kB，**Large** Large class for other objects。其中**Small**被分成了大概70个不同的大小类别，每个大小类别有单独的链表。**Tiny**引入的主要原因是一些小字符串（small strings）以及独立的逃逸变量，Tiny内存分配器会将小对象合并成16bytes大小。

#### Tiny的分配过程（这个过程不是很理解，先照搬过来）
* Look in to corresponding tiny slot object in this P’s mcache.
* Round the size of existing subobject (if exists) into 8, 4 or 2 bytes based on the new object’s size.
* If the object fits together with existing subobjects, place it there.

**If it doesn’t fit in the tiny block:**

* Look in the corresponding mspan in this P’s mcache.
* Obtain a new mspan from mcache.
* Scan the mspan‘s free bitmap to find a free slot.
* If there is a free slot, allocate it and use it as a new tiny slot object. (This can all be done without acquiring a lock.)