---
layout:     post
title:      "关于golang heap"
subtitle:   " \"golang heap使用及实现\""
date:       2019-09-07 09:40:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

> “Somethings Happens”

golang的内置container包中，提供了heap的实现，gopher可以直接拿来用，实现优先级队列，还是挺方便的。本文关注golang实现的一些细节，还有使用的时候应该注意的一些问题。heap的官方文档在这里[golang heap](https://golang.org/pkg/container/heap/)。
golang的堆是基于数组实现的（slice），最大堆的最大值，以及最小堆的最小值都在数组下标为0的位置。跟我们学过的数据结构的堆一致。

首先我们来看一下堆的接口，即若需要将数据结构组织成堆，则需要这个数据结构实现以下接口，注释中以及说明了这个接口中应用的实现：
```go
type Interface interface {
    // 排序需要实现的接口
    sort.Interface
    Push(x interface{}) // add x as element Len()
	Pop() interface{}   // remove and return element Len() - 1.
}
```

golang源代码中提供了一个int类型的heap，代码如下，在我们的Push实现中，可以直接将元素添加到slice末尾，在Pop实现中，直接将末尾的元素移除。注意这里并没有fix操作，如果是这样肯定会对堆得结构造成影响。下面可以直接这么做的原因是，我们不会在代码中直接调用我们的Push和Pop操作，我们提供的Push和Pop操作是供heap包中的heap.Push和heap.Pop函数调用的，这两个函数会对堆的结构进行调整。在我之前的实现中，都是自己在Push和Pop操作中通过`heap.Fix(i Interface, index int)`来调整堆结构的，这样也行，（这样的前提是不要调用heap.Pop以及heap.Push函数了）。

刚刚我们提到过，最大堆的最大值以及最小堆的最小值（即堆的root），都在index为0的位置，heap.Pop操作目的就是将这个下标为0的元素返回并从数组中删除。因此，下面的做法就是先将root跟数组末尾元素交换，然后通过down将新的root进行调整（在down操作中，需要指定最大index为原来的index-1，即将最后一个元素从堆结构中删除）。那在我们自己实现的Pop方法中，只需要将数组最末尾的元素删除就好了。
```go
func Pop(h Interface) interface{} {
	n := h.Len() - 1
	h.Swap(0, n)
	down(h, 0, n)
	return h.Pop()
}
```
golang源代码提供的int堆实现如下。另外需要注意的是，建堆（`heap.Init`）的时间复杂度是O(n)，建堆是从倒数第二层（最后一个非子节点）开始向下调整，调用`down(h, i, n)`，i是当前调整的元素的下标，n是堆结构在数组中的长度（所以从堆结构中删除元素可以调整n的大小，注意此时不是从数组中移除元素）。建堆时，可以通过每一层的节点个数，以及每一层节点的最大调整次数来分析复杂度，结论就是O(n)。
```go
package main

import (
	"container/heap"
	"fmt"
)

// An IntHeap is a min-heap of ints.
type IntHeap []int

func (h IntHeap) Len() int           { return len(h) }
func (h IntHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h IntHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }

func (h *IntHeap) Push(x interface{}) {
	// Push and Pop use pointer receivers because they modify the slice's length,
	// not just its contents.
	*h = append(*h, x.(int))
}

func (h *IntHeap) Pop() interface{} {
	x := (*h)[len(*h)-1]
	*h = (*h)[0 : len(*h)-1]
	return x
}

// This example inserts several ints into an IntHeap, checks the minimum,
// and removes them in order of priority.
func main() {
	h := &IntHeap{2, 1, 5}
	heap.Init(h)
	heap.Push(h, 3)
	heap.Push(h, 0)
	heap.Push(h, -1)
	fmt.Printf("minimum: %d\n", (*h)[0])
	for h.Len() > 0 {
		fmt.Printf("%d ", heap.Pop(h))
	}
}
```