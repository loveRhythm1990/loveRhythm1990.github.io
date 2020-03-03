---
layout:     post
title:      "golang 源码概述"
date:       2020-2-19 18:07:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---


### malloc.go文件中的那一段注释
在golang源码`src/runtime/malloc.go`文件中，开头就是一大段注释，这段注释大概说明了golang内存分配思想，
这里进行翻译一下，golang的版本为1.9.7

内存分配器。

基于tcmalloc，但是稍微做了一些改动[http://goog-perftools.sourceforge.net/doc/tcmalloc.html](http://goog-perftools.sourceforge.net/doc/tcmalloc.html)

主要的分配器是以一连串page来工作的。

小对象（不大于32kB）被round up到大概70个不同大小中的一个，这70个大小中，每个都有一个空闲链表。（Any free page of memory can be split into a set of objects of one size class, which are then managed using a free bitmap. 这句话的意思?，每个空闲的页还可以再划分成一个特定大小的object的集合，这个集合再用bitmap来管理）

分配器的数据结构是：

* fixalloc: a free-list allocator for fixed-size off-heap objects, 用来管理分配器占用的内存。
* mheap: the malloc heap, 以页大小（8192-byte）来管理
* mspan: mheap管理的 a run of pages。（a run of翻译成一连串，但是这个一连串包不包含连续的意思呢？）
* mcentral: 包含不同大小的mspans的集合，每个特定大小的mspan有自己的链表。（collects all spans of a given size class.）
* mcache: pre-P缓存，（a per-P cache of mspans with free space.）
* mstats: 分配统计数据。

分配一个小对象安装下面顺序执行（多级内存分配）：

1. 将大小（假设为size）round up到一个small size大小，并在P的mcache中寻找对应的mspan，遍历mspan的bitmap来查找一个空闲的slot。如果有空闲slot，分配。这一步是不需要锁的。
2. 如果mspan没有free slot，从mcentral的特定大小的mspan列表中获取一个新的mspan，或许一个新的span是需要加锁的。
3. 如果mcentral的mspan列表是空，从mheap中获取一连串page到mcentral中。
4. 如果mheap空间不够，从操作系统中申请内存，（至少申请1M），这个要消耗系统调用的时间。

清空一个mspan以及释放一个object遵循类似的步骤：
1. 当mspan的sweep过程发生在allocation，mspan被分配到mcache以满足allocation。
2. 否则（不是发生在allocation），如果mspan里面仍然有已经分配的对象，这个mspan被放回到mcentral的对应大小的free list中，（Otherwise, if the mspan still has allocated objects in it, it is placed on the mcentral free list for the mspan's size class.）
3. 否则（mspan中所有的object都是free），那么mspan现在是一个`idle`状态，它被归还给mheap，并不再有大小属性了。此时，这个mspan可能跟周围的mspan合并。
4. 如果一个mspan长时间都是idle状态，可以将其返还给操作系统。

分配（以及释放）一个大对象时，会直接使用mheap，并绕过mcache以及mcentral。

mspan中的空闲对象slot只有在`mspan.needzero`的值为false时才被`zeroed`（感觉有点歧义），如果needzero是true，objects只有在分配的时候才被zero，延迟zero的好处有：
1. stack frame 分配，避免一次性全部zero。
2. It exhibits better temporal locality, since the program is probably about to write to the memory.
3. We don't zero pages that never get reused.

### 关键数据结构
**mcache**: per-P cache，可以认为是 local cache。
**mcentral**: 全局 cache，mcache 不够用的时候向 mcentral 申请。
**mheap**: 当 mcentral 也不够用的时候，通过 mheap 向操作系统申请。

可以将其看成多级内存分配器。

#### mcache
每个 Gorontine 的运行都是绑定到一个 P 上面，mcache 是每个 P 的 cache。这么做的好处是分配内存时不需要加锁。mcache 结构如下。mcache源代码在`go/src/runtime/mcache.go`
```golang
// 线程(in Go, per-P)私有的内存空间， for small objects（大对象直接从heap中分配）.
// 因为是线程私有的，所以不需要加锁
//
// mcaches are allocated from non-GC'd memory, so any heap pointers
// must be specially handled.
//
//go:notinheap
type mcache struct {
	// 下面field在每次内存申请时都会被访问到,
	// so they are grouped here for better caching.
	next_sample int32   // trigger heap sample after allocating this many bytes
	local_scan  uintptr // bytes of scannable heap allocated

	// Allocator cache for tiny objects w/o pointers.
	// 参考malloc.go文件中的"Tiny allocator" 注释.

	// tiny points to the beginning of the current tiny block, or
	// nil if there is no current tiny block.
	//
	// tiny is a heap pointer. Since mcache is in non-GC'd memory,
	// we handle it by clearing it in releaseAll during mark
	// termination.
	// tiny是指向当前tiny block的指针
	// 这里说mcache是non-GC memory
	tiny             uintptr
	tinyoffset       uintptr
	local_tinyallocs uintptr // number of tiny allocs not counted in other stats

	// The rest is not accessed on every malloc.

	// 这些是用来分配的span空间
	alloc [numSpanClasses]*mspan // spans to allocate from, indexed by spanClass

	stackcache [_NumStackOrders]stackfreelist

	// Local allocator stats, flushed during GC.
	local_nlookup    uintptr                  // number of pointer lookups
	local_largefree  uintptr                  // bytes freed for large objects (>maxsmallsize)
	local_nlargefree uintptr                  // number of frees for large objects (>maxsmallsize)
	local_nsmallfree [_NumSizeClasses]uintptr // number of frees for small objects (<=maxsmallsize)
}
```
上面代码中提到了`Tiny allocator`，也稍作翻译一下吧
> Tiny allocator. Tiny allocator将多个tiny存储请求合并为一个单独的memory block，这个memory block会在其中所有的tiny object（称为subobject）都不能访问到时（unreachable）被释放，这些subobject必须是`noscan`（不包含指针的），这样保证了可能浪费的内存是有限的。 用来合并的subobject的大小是可调的，参数是`maxTinySize`，目前是16bytes，16bytes会导致2x的最坏的内存浪费（当一个memory block中只有一个subobject能被访问到时），8bytes不会导致内存浪费，但是减少了内存合并的可能性，32byte提供了合并的更大的可能性，但是会导致最大4x的内存浪费，（**这个地方不是很懂**）。 Objects obtained from tiny allocator must not be freed explicitly. So when an object will be freed explicitly, we ensure that its size >= maxTinySize. tiny allocator的主要目标是小字符串，以及一些单独的逃逸变量，在json bench基准脚本中，这个allocator减少了～12%的内存申请，以及~20%的heap大小。

下面看`alloc [numSpanClasses]*mspan`，这里`numSpanClasses`等于`67<<1`，这是一个指针数组，每个数组的元素是指向mspan类型的指针。不同的大小都使用了`mspan`这个结构体，那么其应该记录了当前span对应的object大小。下面看mspan

### mspan
mspan的源代码在`src/runtime/mheap.go`中。
```go
type mspan struct {
	// 双向链表
	next *mspan     // next span in list, or nil if none
	prev *mspan     // previous span in list, or nil if none
	list *mSpanList // For debugging. TODO: Remove.

	// span的第一个字节的地址
	startAddr uintptr // address of first byte of span aka s.base()
	// span中的页数
	npages    uintptr // number of pages in span

	manualFreeList gclinkptr // list of free objects in _MSpanManual spans

	// freeindex is the slot index between 0 and nelems at which to begin scanning
	// for the next free object in this span.
	// Each allocation scans allocBits starting at freeindex until it encounters a 0
	// indicating a free object. freeindex is then adjusted so that subsequent scans begin
	// just past the newly discovered free object.
	//
	// If freeindex == nelem, this span has no free objects.
	//
	// allocBits is a bitmap of objects in this span.
	// If n >= freeindex and allocBits[n/8] & (1<<(n%8)) is 0
	// then object n is free;
	// otherwise, object n is allocated. Bits starting at nelem are
	// undefined and should never be referenced.
	//
	// Object n starts at address n*elemsize + (start << pageShift).
	freeindex uintptr
	// TODO: Look up nelems from sizeclass and remove this field if it
	// helps performance.
	nelems uintptr // number of object in the span.

	// Cache of the allocBits at freeindex. allocCache is shifted
	// such that the lowest bit corresponds to the bit freeindex.
	// allocCache holds the complement of allocBits, thus allowing
	// ctz (count trailing zero) to use it directly.
	// allocCache may contain bits beyond s.nelems; the caller must ignore
	// these.
	allocCache uint64

	// allocBits and gcmarkBits hold pointers to a span's mark and
	// allocation bits. The pointers are 8 byte aligned.
	// There are three arenas where this data is held.
	// free: Dirty arenas that are no longer accessed
	//       and can be reused.
	// next: Holds information to be used in the next GC cycle.
	// current: Information being used during this GC cycle.
	// previous: Information being used during the last GC cycle.
	// A new GC cycle starts with the call to finishsweep_m.
	// finishsweep_m moves the previous arena to the free arena,
	// the current arena to the previous arena, and
	// the next arena to the current arena.
	// The next arena is populated as the spans request
	// memory to hold gcmarkBits for the next GC cycle as well
	// as allocBits for newly allocated spans.
	//
	// The pointer arithmetic is done "by hand" instead of using
	// arrays to avoid bounds checks along critical performance
	// paths.
	// The sweep will free the old allocBits and set allocBits to the
	// gcmarkBits. The gcmarkBits are replaced with a fresh zeroed
	// out memory.
	allocBits  *gcBits
	gcmarkBits *gcBits

	// sweep generation:
	// if sweepgen == h->sweepgen - 2, the span needs sweeping
	// if sweepgen == h->sweepgen - 1, the span is currently being swept
	// if sweepgen == h->sweepgen, the span is swept and ready to use
	// h->sweepgen is incremented by 2 after every GC

	sweepgen    uint32
	divMul      uint16     // for divide by elemsize - divMagic.mul
	baseMask    uint16     // if non-0, elemsize is a power of 2, & this will get object allocation base
	allocCount  uint16     // number of allocated objects
	spanclass   spanClass  // size class and noscan (uint8)
	incache     bool       // being used by an mcache
	state       mSpanState // mspaninuse etc
	needzero    uint8      // needs to be zeroed before allocation
	divShift    uint8      // for divide by elemsize - divMagic.shift
	divShift2   uint8      // for divide by elemsize - divMagic.shift2
	elemsize    uintptr    // computed from sizeclass or from npages
	unusedsince int64      // first time spotted by gc in mspanfree state
	npreleased  uintptr    // number of pages released to the os
	limit       uintptr    // end of data in span
	speciallock mutex      // guards specials list
	specials    *special   // linked list of special records sorted by offset.
}
```