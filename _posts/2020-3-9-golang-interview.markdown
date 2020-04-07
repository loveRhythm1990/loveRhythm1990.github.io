---
layout:     post
title:      "golang常见面试题整理"
date:       2020-03-09 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

##### struct以及interface的比较
golang官方对于此问题的描述在[Comparison operators](https://golang.org/ref/spec#Comparison_operators)，这部分操作符有两种：
* 判断是否相等（comparable），==， ！=
* 排序（ordered），<，>，<=，>=

先看comparable，一般基础类型都是可以判断是否相等的，比如Boolean, Integer, Floating-point, Complex, String, Pointer
。

对于Pointer来说，判等运算就是比较的指针的指，如果两个指针指向相同的内存地址，则Pointer相等，当两个指针都是nil的时候也是相等的，这里要求是同类型的，下面的pa以及pb是不能判等的，因为是不同类型：
```go
	var pa *int
	var pb *string
	fmt.Println(pa == pb) // syntax error
```

Channel也是可判等的，条件也是同类型的，chan int与chan string是不能判等的，这个ide会直接提示语法错误，如果两个channel是用同一个make函数生成的，则是相等的，两个同类型的nil channel也是相等的。

Interface也可是可以判等的，两个interface是相等的，如果他们有`identical`的动态类型，以及相等的动态类型值，或者他们都是nil，这里先解释一下[identical](https://golang.org/ref/spec#Type_identity):
文档中说，两个类型，要么是`identical`要么是`different`，两个**命名类型**是否相同的标准如下：
1. 两个命名类型相同的条件是两个类型声明的语句完全相同，关于这点，官网上有句话`A defined type is always different from any other type`，那是否可以理解成命名类型跟其他类型都是非identical的
2. 命名类型和未命名类型永远不相同
3. 两个未命名类型相同的条件是他们的类型声明字面量的结构相同，并且内部元素的类型相同，前一句可以参考官网：对于struct类型来说，如果他们的field顺序是相同的，并且每个field的名字以及类型都是相同的，并且有相同的tag
4. 通过类型别名语句声明的两个类型相同，类型别名的语法为：`type T1 = T2`，T1和T2完全一样。

一个非interface类型跟interface也是可比较的，如果这个非interface类型实现了这个interface，并且interface的动态类型跟前者相同，并且值也相同。

如果struct类型的field是可比较的，那么struct也是可比较的，并且如果field相等，那么struct也是相等的，struct类型比较也是要求类型是`identical`的，下面的语句也会有语法错误：
```go
type ss1 struct {
	a int
	b int
}

type ss2 struct {
	a int
	b int
}

func main()  {
	v1 := ss2{}
	v2 := ss1{}
	fmt.Println(v1 == v2) // error, mismatched type ss1 and ss2
}
```

数组是可以比较的，如果数组的元素是可比较的，每个元素equal，那么数组也equal。

另外需要注意的是：如果interface的动态类型相同，但是动态类型的值不支持比较，会引起运行时panic，这个规则适用于interface或者是含有interface的struct。这个可以用下面例子验证：
```go
type ss1 []int

func (s ss1)Swap(i, j int) {}

func main()  {
	var i1 interface{}
	var i2 interface{}

	i1 = ss1{}
	i2 = ss1{}
	fmt.Println(i1 == i2)
}
```
程序是不报语法错误的，但是运行时会panic，信息为：`panic: runtime error: comparing uncomparable type main.ss1`

最后，`Slice` `Map`以及`function`是不可比较的，但是他们的值可以用nil比较。下面程序会直接报错：`operator == not defined on []int`
```go
	var i1 []int
	var i2 []int

	fmt.Println(i1 == i2) // error
```

##### 关于select
select语句是一种仅能用于通道发送和接收操作的语句。一条selector语句执行时，会选择其中的某个分支并执行。如果select语句中的所有普通case都不满足条件，并且有default，那么default语句会被选中，否则select会阻塞。一个select语句只能包含一个default case，不过它可以放置在该语句的任何位置上。

在开始执行select语句的时候，所有跟在case关键字右边的发送语句或接收语句中的通道表达式或者元素表达式都会被求值，（求值顺序是从左到右，自上而下），无论它所在的case是否有可能被选择都是这样。当有多个case符合条件时，运行时系统会通过一个伪随机的算法选择一个case，其他case会被忽略

##### 短变量声明
使用冒号加等号实现短变量声明，即`a := 3`，由golang运行时系统自动推断类型，短变量声明一般只出现在函数内部，也在`if`,`for`或者`switch`语句中，用于声明临时变量，（全局变量是没办法这么声明的），另外，使用var与短变量声明都是可以自己推断类型的。

##### make与new的区别
内置函数`new`接收一个类型参数，在运行时为其分配一个存储空间，并返回一个指向存储空间的指针，new分配的空间也会被初始化为0值。

内置函数`make`只用于初始化`slice`,`map`,以及`channel`，并可以接收长度等可选参数。make返回的值，并不是指针，分配的空间也是初始化过的。关于为什么使用`make`，这里有自己的一点思考，对于slice来说，其结构可以看做（定义在`src/reflect/value.go`）：
```go
// SliceHeader is the runtime representation of a slice.
// It cannot be used safely or portably and its representation may
// change in a later release.
// Moreover, the Data field is not sufficient to guarantee the data
// it references will not be garbage collected, so programs must keep
// a separate, correctly typed pointer to the underlying data.
type SliceHeader struct {
	Data uintptr
	Len  int
	Cap  int
}
```
也就是说，仅仅需要用new初始化是不够的，需要额外做一些初始化的工作，比如底层数组的布局等。

与此相关联的一个问题是：

`通过new分配空间，与通过变量声明的方式有什么区别？`
```go
i := new(int)

// get a pointer from a variable
var v int
i := &v
```
答案是几乎没有区别的，都是最终调用了`runtime.newobject`来分配空间的。 


##### atomic.Value的实现
atomic.Value底层是一个空接口类型，对应两个字段，即类型与数据指针：
```go
type Value struct {
	v interface{}
}

// ifaceWords is interface{} internal representation.
// 这个跟runtime/types.go中eface类型是一致的
type ifaceWords struct {
	typ  unsafe.Pointer
	data unsafe.Pointer
}
```
atomic.Value只有Load以及Store两个接口，在Store中，多次Store的变量类型必须是一致的，否则会panic。在Store接口中，实际要设置两个值，及`typ`类型指针，以及`data`数据指针。
为了完成多个字段的原子性写入，可以抓住其中的一个字段，以它的状态来标志整个原子写入的状态。

并且在Store接口中，使用了一个中间指针`^uintptr(0)`，这个当初看的有点奇怪，主要用来标示这个Value是否处在被设置的中间状态。在Load接口中，检查到这个指针就返回nil，标示有store处于中间过程。

atomic的实现中，只涉及到对指针的操作。

参考[Go 语言标准库中 atomic.Value 的前世今生](https://blog.betacat.io/post/golang-atomic-value-exploration/)

##### sync.Map的实现
同时读写一个普通的map会发生panic:`fatal error: concurrent map read and map write`，写时写，遍历时写，删除时写也会发生panic。

关于sync.Map有如下几个要点：
1. 空间换时间。 通过冗余的两个数据结构(read、dirty),实现加锁对性能的影响。
2. 使用只读数据(read)，避免读写冲突。
3. 动态调整，miss次数多了之后，将dirty数据提升为read。
4. double-checking。
5. 延迟删除。 删除一个键值只是打标记，只有在提升dirty的时候才清理删除的数据。
6. 优先从read读取、更新、删除，因为对read的读取不需要锁。

sync.Map数据结构：
```go
type Map struct {
	// 当涉及到dirty数据的操作的时候，需要使用这个锁
	mu Mutex

	// 一个只读的数据结构，因为只读，所以不会有读写冲突。
	// 所以从这个数据中读取总是安全的。
	// 实际上，实际也会更新这个数据的entries,如果entry是未删除的(unexpunged), 并不需要加锁。如果entry已经被删除了，需要加锁，以便更新dirty数据。
	// 这是个原子类型
	read atomic.Value // readOnly

	// dirty数据包含当前的map包含的entries,它包含最新的entries(包括read中未删除的数据,虽有冗余，但是提升dirty字段为read的时候非常快，不用一个一个的复制，而是直接将这个数据结构作为read字段的一部分),有些数据还可能没有移动到read字段中。
	// 对于dirty的操作需要加锁，因为对它的操作可能会有读写竞争。
	// 当dirty为空的时候， 比如初始化或者刚提升完，下一次的写操作会复制read字段中未删除的数据到这个数据中。
	dirty map[interface{}]*entry

	// 当从Map中读取entry的时候，如果read中不包含这个entry,会尝试从dirty中读取，这个时候会将misses加一，
	// 当misses累积到 dirty的长度的时候， 就会将dirty提升为read,避免从dirty中miss太多次。因为操作dirty需要加锁。
	misses int
}
```
read的数据类型如下，atomic.Value只能读取和Set一种类型，那在这里就是这个readOnly类型，在atomic.Value这个数据结构中，会有两个指针分别指向readOnly的类型，以及其数据。
```go
type readOnly struct {
	// 是实际底层map中，存的都是指针
	m       map[interface{}]*entry
	amended bool // 如果Map.dirty有些数据不在Map.read中的时候，这个值为true
}
```
amended指明Map.dirty中有readOnly.m未包含的数据，所以如果从Map.read找不到数据的话，还要进一步到Map.dirty中查找。

对Map.read的修改是通过原子操作进行的。

虽然read和dirty有冗余数据，但这些数据是通过指针指向同一个数据，所以尽管Map的value会很大，但是冗余的空间占用还是有限的。

readOnly.m和Map.dirty存储的值类型是*entry,它包含一个指针p, 指向用户存储的value值。所有用户设置的Value，都是通过entry这个结构来指向的。所以sync.Map中冗余指的是指针冗余。
```go
type entry struct {
	p unsafe.Pointer // *interface{}
}
```
p有三种值：

* nil: entry已被删除了，并且m.dirty为nil
* expunged: entry已被删除了，并且m.dirty不为nil，而且这个entry不存在于m.dirty中，那就是在m.read结构中
* 其它： entry是一个正常的值
其他内容，下面参考文档说的很详细，已存坚果云。

这里只看存储实现：
```go
func (m *Map) Store(key, value interface{}) {
	read, _ := m.read.Load().(readOnly)
	if e, ok := read.m[key]; ok && e.tryStore(&value) {
		return
	}

	m.mu.Lock()
	read, _ = m.read.Load().(readOnly)
	if e, ok := read.m[key]; ok {
		if e.unexpungeLocked() {
			// The entry was previously expunged, which implies that there is a
			// non-nil dirty map and this entry is not in it.
			m.dirty[key] = e
		}
		e.storeLocked(&value)
	} else if e, ok := m.dirty[key]; ok {
		e.storeLocked(&value)
	} else {
		if !read.amended {
			// We're adding the first new key to the dirty map.
			// Make sure it is allocated and mark the read-only map as incomplete.
			m.dirtyLocked()
			m.read.Store(readOnly{m: read.m, amended: true})
		}
		m.dirty[key] = newEntry(value)
	}
	m.mu.Unlock()
}

// tryStore stores a value if the entry has not been expunged.
//
// If the entry is expunged, tryStore returns false and leaves the entry
// unchanged.
func (e *entry) tryStore(i *interface{}) bool {
	for {
		p := atomic.LoadPointer(&e.p)
		if p == expunged {
			return false
		}
		// 如果entry不是expunged，则尝试修改，
		if atomic.CompareAndSwapPointer(&e.p, p, unsafe.Pointer(i)) {
			return true
		}
	}
}
```

参考[Go 1.9 sync.Map揭秘](https://colobu.com/2017/07/11/dive-into-sync-Map/)
##### 协称、线程、进程的区别


##### golang编译相关问题，静态编译/动态编译

##### 如何理解"不要通过共享内存来通信，而应该通过通信来共享内存"

##### for-range实现相关


参考：

[Golang 线程和协程的区别](https://segmentfault.com/q/1010000004878639)

[你遇到过哪些高质量的 Go 语言面试题？](https://www.zhihu.com/question/60952598)

[Golang语言面试题-go夜读](https://reading.developerlearning.cn/interview/interview-golang-language/)