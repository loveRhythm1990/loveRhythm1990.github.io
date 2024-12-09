---
layout:     post
title:      "go-cache:一个简单内存 cache 设计"
date:       2021-02-10 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

**文章目录**
- [go-cache 概述](#go-cache-概述)
- [实现细节](#实现细节)
	- [数据结构](#数据结构)
	- [初始化](#初始化)
	- [Set/Add/Get](#setaddget)
	- [Save（备份到文件）](#save备份到文件)
- [总结](#总结)


### go-cache 概述
[https://github.com/patrickmn/go-cache](https://github.com/patrickmn/go-cache) 是一个简单的 go 内存缓存，但是 star 数较多，可见受到大家的认可。本文主要探讨一下 go-cache 的设计思路，看一下一个 cache 要设计到什么程度，不是越简单越好，也不是越复杂也好。

通过官方文档，我们了解到 go-cache 有下面特点（功能）：
* 内存 cache：所有的数据都放在内存中，具体是一个 `map[string]interface{}` 中。
* 适合单应用使用：不涉及多应用之间共享 cache。
* 线程安全：多线程可同时存取数据，通过给 map 加锁实现。
* 支持 key/value 过期：set 的时候可以指定过期时间，如果不指定，则使用初始化 cache 时的默认过期时间。
* 支持将 cache dump 到文件进行备份：通过 golang 中的 gob 进行序列化，性能优于 json。
* 支持在删除时触发特定的 action，类似于 hook。
* 支持将所有的算数类型加上特定数值或者减去特定数值。

### 实现细节
go-cache 本身实现比较简单，大概浏览一下。

#### 数据结构

go-cache 涉及到三个数据结构：`Item`、`cache`、`janitor`，分别看一下其作用。

**Item** 表示 map 缓存的 value，Object 可以是任意类型，带有一个过期时间戳。
```go
type Item struct {
	Object     interface{}
	Expiration int64
}
```
**cache** 是核心数据结构，其内容如下，由此可见，这个缓存只能配置一个 onEvicted 函数，其中 onEvicted 的参数为 cache 中的 key 和 Object，key 和 Object 可用来过滤 Item。
```go
type cache struct {
	defaultExpiration time.Duration
	items             map[string]Item
	mu                sync.RWMutex
	onEvicted         func(string, interface{})
	janitor           *janitor
}
```
**janitor** 是用来清除过期的 key 的结构，在使用 New 初始化 go-cache 的时候，同时会初始化一个 janitor goroutine，用来定期删除过期数据，其中 ticker 的周期就是 Interval。
```go
type janitor struct {
	Interval time.Duration
	stop     chan bool
}
```

#### 初始化
初始化需要两个参数：1）默认过期时间；2）清理过期时间的 interval。同时会启动 Janitor goroutine 来定期清理过期数据。其中 DeleteExpired 是把所有数据枚举一遍，并检查是否过期，过期则删除。

其中 janitor 初始化的时候有个地方比较 trick，就是如何停止回收过期数据的 goroutine，janitor 中有个同步 channel `stop: make(chan bool)`，当这个 channel 可读的时候，则停止 goroutine。那什么时候这个 channel 可读呢？go-cache 调用了 `runtime.SetFinalizer(C, stopJanitor)` 方法，表示整个 Cache 没有人在用，在进行垃圾回收的时候，goroutine 就应该停止。

一般来讲，我们应用在使用 cache 的时候，cache 的生命周期应该是跟应用一致的，这个时候不需要主动停止 goroutine（或者我们在应用停止的时候调用 stop），`runtime.SetFinalizer(C, stopJanitor)` 的作用应该是应用中的局部 cache，当局部 cache 因为没有引用而被垃圾回收期回收时，就会调用 `stopJanitor`，避免了 goroutine 泄露。

```go
func newCacheWithJanitor(de time.Duration, ci time.Duration, m map[string]Item) *Cache {
	c := newCache(de, m)
	C := &Cache{c}
	if ci > 0 {
		runJanitor(c, ci)
		runtime.SetFinalizer(C, stopJanitor)
	}
	return C
}
func New(defaultExpiration, cleanupInterval time.Duration) *Cache {
	items := make(map[string]Item)
	return newCacheWithJanitor(defaultExpiration, cleanupInterval, items)
}
func (j *janitor) Run(c *cache) {
	ticker := time.NewTicker(j.Interval)
	for {
		select {
		case <-ticker.C:
			c.DeleteExpired()
		case <-j.stop:
			ticker.Stop()
			return
		}
	}
}
func stopJanitor(c *Cache) { c.janitor.stop <- true }

func runJanitor(c *cache, ci time.Duration) {
	j := &janitor{
		Interval: ci,
		stop:     make(chan bool),
	}
	c.janitor = j
	go j.Run(c)
}
func newCache(de time.Duration, m map[string]Item) *cache {
	if de == 0 {de = -1}
	c := &cache{
		defaultExpiration: de,
		items:             m,
	}
	return c
}
```
#### Set/Add/Get
Set 以及 Add 用来添加元素，其中语义不同，Set 用于添加或者替换，Add 则只用于添加，如果元素已经存在则报错。类似的情况还有 Replace，要求元素一定存在。

有个细节可以注意一下，为了提高效率，Add 等方法没有使用 `defer` 关键字进行解锁。
```go
func (c *cache) Add(k string, x interface{}, d time.Duration) error {
	c.mu.Lock()
	_, found := c.get(k)
	if found {
		c.mu.Unlock()
		return fmt.Errorf("Item %s already exists", k)
	}
	c.set(k, x, d)
	c.mu.Unlock()
	return nil
}
```

Get 接口不会报错，有两个返回参数：1）cache value（不存在或者过期就返回 nil）；2）标识位，是否存在 key。具体有三种情况：
1. 元素不存在，返回 nil, false。
2. 元素存在，但是过期了，当做不存在处理，返回 nil, false。
3. 元素存在，并且没有过期，返回 object, true。

```go
func (c *cache) Get(k string) (interface{}, bool) {
	c.mu.RLock()
	// "Inlining" of get and Expired
	item, found := c.items[k]
	if !found {
		c.mu.RUnlock()
		return nil, false
	}
	if item.Expiration > 0 {
		if time.Now().UnixNano() > item.Expiration {
			c.mu.RUnlock()
			return nil, false
		}
	}
	c.mu.RUnlock()
	return item.Object, true
}
```

#### Save（备份到文件）
go-cache 支持将所有缓存数据放到文件中，这里用到了 golang 中特有的编解码库 gob，gob 的作用跟 json 或者 protobuf 是一样的，不过跟 json 相比效率更高一点，因为是编码为二进制，当前就没有可读性。
```go
func (c *cache) Save(w io.Writer) (err error) {
	enc := gob.NewEncoder(w)
	defer func() {
		if x := recover(); x != nil {
			err = fmt.Errorf("Error registering item types with Gob library")
		}
	}()
	c.mu.RLock()
	defer c.mu.RUnlock()
	for _, v := range c.items {
		gob.Register(v.Object)
	}
	err = enc.Encode(&c.items)
	return
}
```
下面是使用 gob 编解码的示例，在使用 gob 编解码之前需要将所有要编解码的类型注册到 gob 中。初始化 encoder 需要一个 `io.Writer`，表示输出流，即编码之后要写到哪个地方；初始化 decoder 需要一个 `io.Reader`，表示要从哪里读字节流。
```go
const file = "/Users/cachefile.txt"
type Foo struct {
	Name string
	ID   int
}
func main() {
	f, err := os.Create(file)
	lo.Must0(err) // "github.com/samber/lo"
	defer func() { _ = f.Close() }()

	cache := map[string]interface{}{}
	cache["a"] = "a-value"
	cache["b"] = Foo{"name", 1}

	for _, v := range cache {
		gob.Register(v)
	}
	encoder := gob.NewEncoder(f)
	lo.Must0(encoder.Encode(cache))

	// load from file
	for k, v := range load(file) {
		fmt.Printf("%s: %v\n", k, v)
	}
}

func load(file string) map[string]interface{} {
	f, err := os.Open(file)
	lo.Must0(err)
	defer func() { _ = f.Close() }()

	cache := map[string]interface{}{}
	lo.Must0(gob.NewDecoder(f).Decode(&cache))
	return cache
}
```

### 总结
简单很重要，语义清晰也很重要。