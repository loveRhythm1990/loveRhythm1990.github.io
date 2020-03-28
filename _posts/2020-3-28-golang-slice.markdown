---
layout:     post
title:      "golang slice总结"
date:       2020-03-25 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

关注slice的具体实现，基于的golang版本为go1.12.1

#### 参数为slice指针与参数为slice的区别
传递slice的指针与传递slice都能够影响函数外边的slice的元素值，也就是说，我们在函数内部修改了元素值，函数外面也能看到，这就是副作用。但是传递指针的时候，副作用不只是元素值，slice本身的结构信息也能被修改，比如len，实际上我们传递指针的时候，完全可以改变指针的指向，使外面的slice完全变成另一个slice，如下所示：
```go
func main() {
	arrayA := []int{100, 200}
	fmt.Printf("arrayA : %p , %v\n", &arrayA, arrayA)

	testPointer(&arrayA)
	fmt.Printf("arrayA : %p , %v\n", &arrayA, arrayA)
}

func testPointer(x *[]int) {
	newSlice := []int{12, 12, 13}
	*x = newSlice
}
```
输出如下，从输出看，arrayA完全变成了另一个slice：
```s
arrayA : 0xc00000c080 , [100 200]
arrayA : 0xc00000c080 , [12 12 13]
```
当我们传递一个slice时，实际上是传递了一个结构体，我们可以改变元素的值（通过这个结构体里的那个指针），但是不能够改变结构信息。
```go
type sliceHeader struct {
	Data unsafe.Pointer
	Len  int
	Cap  int
}
```
博客[深入解析 Go 中 Slice 底层实现](https://halfrost.com/go_slice/)提到了一个例外情况：`数组在栈上分配时，slice在堆上分配时，数组可能比slice快`。（有一点需要分析一下，数组的值拷贝方法，也就是copy方法，分析其代价开销）。使用的测试程序如下：
```go
package main

import "testing"

func array() [1024]int {
	var x [1024]int
	for i := 0; i < len(x); i++ {
		x[i] = i
	}
	return x
}

func slice() []int {
	x := make([]int, 1024)
	for i := 0; i < len(x); i++ {
		x[i] = i
	}
	return x
}

func BenchmarkArray(b *testing.B) {
	for i := 0; i < b.N; i++ {
		array()
	}
}

func BenchmarkSlice(b *testing.B) {
	for i := 0; i < b.N; i++ {
		slice()
	}
}
```
使用命令`go test -bench . -benchmem -gcflags "-N -l"`运行，结果如下：
```s
BenchmarkArray-4          500000              3378 ns/op               0 B/op          0 allocs/op
BenchmarkSlice-4          300000              5260 ns/op            8192 B/op          1 allocs/op
```
在测试 Array 的时候，用的是4核，循环次数是500000，平均每次执行时间是3378ns，每次执行堆上分配内存总量是0，分配次数也是0。

而切片的结果就“差”一点，同样也是用的是4核，循环次数是300000，平均每次执行时间是5260ns，但是每次执行一次，堆上分配内存总量是8192，分配次数也是1 。

将`array`, `slice`两个函数拷贝到代码文件中，使用命令`go build -gcflags="-m -m"`查看内存逃逸情况，发现`make([]int, 1024)`是分配在heap中的。
```s
./main.go:13:6: cannot inline array: unhandled op FOR
./main.go:21:6: cannot inline slice: unhandled op FOR
./main.go:10:27: unsafe.Sizeof(a) escapes to heap
./main.go:10:27:        from ~arg0 (assign-pair) at ./main.go:10:13
./main.go:10:13: io.Writer(os.Stdout) escapes to heap
./main.go:10:13:        from io.Writer(os.Stdout) (passed to call[argument escapes]) at ./main.go:10:13
./main.go:10:13: main []interface {} literal does not escape
./main.go:22:11: make([]int, 1024) escapes to heap
./main.go:22:11:        from x (assigned) at ./main.go:22:4
./main.go:22:11:        from ~r0 (return) at ./main.go:26:2
```
之前在逃逸分析的博客说明`当分配的空间太大时，可能会在堆中分配`，实际上在上面的代码中，就算我将数组大小改为[10240]int，根据一个int占用8个字节，10240*8 byte仍然没有发生逃逸行为，`难道是因为数组在传递时使用值拷贝，逃逸没有意义？因为每次传递都是做一个副本`，当然我传递数组的指针时，这个时候在函数外是要访问数组的，这个时候肯定发生逃逸，也就是下面的情况：
```go
//发生逃逸
func array() *[1024]int {
	var x [1024]int
	for i := 0; i < len(x); i++ {
		x[i] = i
	}
	return &x
}
```

#### 捏造一个slice
自己动手，丰衣足食，我们已经知道slice的结构，下面我们要自己构造一个
```go
func main()  {
	slice := mySlice()

	fmt.Println(reflect.TypeOf(slice))
	fmt.Println(slice[0])
}

func mySlice() []int {
	arr := [5]int{1,2,3,4,5}
	mySlice := struct {
		pointer unsafe.Pointer
		length int
		cap    int
	}{}

	mySlice.pointer = unsafe.Pointer(&arr)
	mySlice.length = len(arr)
	mySlice.cap = len(arr)

	slice := *(*[]int)(unsafe.Pointer(&mySlice))
	return slice
}
```
输出为：
```s
[]int
1
```
上面利用了两点：
* 自定义的struct其结构跟reflect.SliceHeader一致。
* unsafe.Pointer可以转化为任意类型的指针，任意类型的指针可以转化为unsafe.Pointer

#### make slice
slice相关的代码在`src/runtime/slice.go`文件中，代码如下：
```go
func makeslice(et *_type, len, cap int) unsafe.Pointer {
    // 根据容量和类型大小，确定需要的内存
    // _type是golang所有类型的底层类型
	mem, overflow := math.MulUintptr(et.size, uintptr(cap))
	if overflow || mem > maxAlloc || len < 0 || len > cap {
		mem, overflow := math.MulUintptr(et.size, uintptr(len))
		if overflow || mem > maxAlloc || len < 0 {
			panicmakeslicelen()
		}
		panicmakeslicecap()
	}

    // 直接调用mallocgc，needzero设置为true
	return mallocgc(mem, et, true)
}
```

#### copy
golang内置函数copy的签名为：`func copy(dst, src []Type) int`，解释为：
> 内置函数copy将元素从源slice拷贝到目的slice，（作为一种特殊情况，copy还可以将string拷贝到[]byte里），源slice与目标slice可能重叠，copy返回复制的element个数，个数为len(src)以及len(dst)的最小值。

golang 1.12对于slice的copy实现如下：
```go
// slice.go文件中对于slice的定义
type slice struct {
	array unsafe.Pointer
	len   int
	cap   int
}
func slicecopy(to, fm slice, width uintptr) int {
	if fm.len == 0 || to.len == 0 {
		return 0
	}

	n := fm.len
	if to.len < n {
		n = to.len
	}

	if width == 0 {
		return n
	}

    // 开启静态检测
	if raceenabled {
		callerpc := getcallerpc()
		pc := funcPC(slicecopy)
		racewriterangepc(to.array, uintptr(n*int(width)), callerpc, pc)
		racereadrangepc(fm.array, uintptr(n*int(width)), callerpc, pc)
	}
	if msanenabled {
		msanwrite(to.array, uintptr(n*int(width)))
		msanread(fm.array, uintptr(n*int(width)))
	}

	size := uintptr(n) * width
	if size == 1 { // common case worth about 2x to do here
        // TODO: is this still worth it with new memmove impl?
        // array实际是执行数组首地址的指针，因为这里计算了下，总共只需要拷贝一个字节，
        // 也就是只有一个byte类型，那这里直接进行了强制类型转化，把数组首指针转化为一个byte
        // 数组首指针也是第一个元素的地址
		*(*byte)(to.array) = *(*byte)(fm.array) // known to be a byte pointer
	} else {
		memmove(to.array, fm.array, size)
	}
	return n
}
```
由此可看，slicecopy主要是`memmove`实现的，这个是用汇编实现的`src/runtime/memmove_amd64.s`，看不懂
```go
	arr := [1]byte{2}
	b := *(*byte)(unsafe.Pointer(&arr))
    fmt.Println(b)
    // 输出2
```

#### grow slice
`growslice`处理append过程中slice的增长，参数为：元素类型，旧的slice，期望的新的slice的最小长度。返回值是一个新的slice，其capacity至少为参数指定的值，并且包含旧的slice的数据。
新的slice的长度与旧的slice的长度是相等的。实现如下：
```go
func growslice(et *_type, old slice, cap int) slice {
    
    //开头这两个if没看懂
    if raceenabled {
		callerpc := getcallerpc()
		racereadrangepc(old.array, uintptr(old.len*int(et.size)), callerpc, funcPC(growslice))
	}
	if msanenabled {
		msanread(old.array, uintptr(old.len*int(et.size)))
	}

	if cap < old.cap {
		panic(errorString("growslice: cap out of range"))
	}

	if et.size == 0 {
		// append should not create a slice with nil pointer but non-zero len.
		// We assume that append doesn't need to preserve old.array in this case.
		return slice{unsafe.Pointer(&zerobase), old.len, cap}
	}

    newcap := old.cap
    // 首先将旧的容量翻倍
	doublecap := newcap + newcap
	if cap > doublecap {
        // 如果期望的大小比原来的两倍还大，设置为期望的大小
		newcap = cap
	} else {
		if old.len < 1024 {
            // 原来的旧的slice的大小小于1024，那就用原来大小的两倍了
			newcap = doublecap
		} else {
			// Check 0 < newcap to detect overflow
            // and prevent an infinite loop.
            // 如果原来的旧的slice的大小大于1024，一直在原来的基础上增加1/4，
            // 直到增大到cap的大小
			for 0 < newcap && newcap < cap {
				newcap += newcap / 4
			}
			// Set newcap to the requested cap when
			// the newcap calculation overflowed.
			if newcap <= 0 {
				newcap = cap
			}
		}
	}

    var overflow bool
    // lenmem表示所有旧的element占用的大小，这部分是需要拷贝到新的slice中去的
    // newlenmem表示新的cap乘以元素大小，
    // capmem表示最终需要申请的内存的大小（newcap * element.size），这部分是需要调用mallocgc申请的
    // capmem是要大于等于newlenmem的
	var lenmem, newlenmem, capmem uintptr
    // 为一些特殊的元素大小做了特殊处理，以优化性能
	switch {
	case et.size == 1:
		lenmem = uintptr(old.len)
		newlenmem = uintptr(cap)
		capmem = roundupsize(uintptr(newcap))
		overflow = uintptr(newcap) > maxAlloc
		newcap = int(capmem)
	case et.size == sys.PtrSize:
		lenmem = uintptr(old.len) * sys.PtrSize
		newlenmem = uintptr(cap) * sys.PtrSize
		capmem = roundupsize(uintptr(newcap) * sys.PtrSize)
		overflow = uintptr(newcap) > maxAlloc/sys.PtrSize
		newcap = int(capmem / sys.PtrSize)
	case isPowerOfTwo(et.size):
		var shift uintptr
		if sys.PtrSize == 8 {
			// Mask shift for better code generation.
			shift = uintptr(sys.Ctz64(uint64(et.size))) & 63
		} else {
			shift = uintptr(sys.Ctz32(uint32(et.size))) & 31
		}
		lenmem = uintptr(old.len) << shift
		newlenmem = uintptr(cap) << shift
		capmem = roundupsize(uintptr(newcap) << shift)
		overflow = uintptr(newcap) > (maxAlloc >> shift)
		newcap = int(capmem >> shift)
	default:
		lenmem = uintptr(old.len) * et.size
		newlenmem = uintptr(cap) * et.size
		capmem, overflow = math.MulUintptr(et.size, uintptr(newcap))
		capmem = roundupsize(capmem)
		newcap = int(capmem / et.size)
	}

	// The check of overflow in addition to capmem > maxAlloc is needed
	// to prevent an overflow which can be used to trigger a segfault
	// on 32bit architectures with this example program:
	//
	// type T [1<<27 + 1]int64
	//
	// var d T
	// var s []T
	//
	// func main() {
	//   s = append(s, d, d, d, d)
	//   print(len(s), "\n")
	// }
	if overflow || capmem > maxAlloc {
		panic(errorString("growslice: cap out of range"))
	}

	var p unsafe.Pointer
	if et.kind&kindNoPointers != 0 {
        // 调用mallocgc分配内存
		p = mallocgc(capmem, nil, false)
		// The append() that calls growslice is going to overwrite from old.len to cap (which will be the new length).
		// Only clear the part that will not be overwritten.
		memclrNoHeapPointers(add(p, newlenmem), capmem-newlenmem)
	} else {
		// Note: can't use rawmem (which avoids zeroing of memory), because then GC can scan uninitialized memory.
		p = mallocgc(capmem, et, true)
		if writeBarrier.enabled {
			// Only shade the pointers in old.array since we know the destination slice p
			// only contains nil pointers because it has been cleared during alloc.
			bulkBarrierPreWriteSrcOnly(uintptr(p), uintptr(old.array), lenmem)
		}
    }
    // 调用memmove拷贝内存
	memmove(p, old.array, lenmem)

	return slice{p, old.len, newcap}
}

```

#### 参考

[深入解析 Go 中 Slice 底层实现](https://halfrost.com/go_slice/)

[听说你想用go汇编写memmove](https://mzh.io/writing-go-memmove-asm/)