---
layout:     post
title:      "bbolt 初体验[转载]"
date:       2021-06-20 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - etcd
    - bbolt
---

本文转载于 晒太阳的猫 [《bbolt初体验》](https://zhengyinyong.com/post/bbolt-first-experience/)

### 什么是 bbolt
[bbolt](https://github.com/etcd-io/bbolt) 是对 Ben Johnson 的 [bolt](https://github.com/boltdb/bolt) 项目的 fork ，目前被应用于 etcd 中作为底层存储。bbolt 是一个**嵌入式的 KV 存储库**，其作用类似于基于 C++ 的 LevelDB，但二者采用了不同的核心数据结构，**前者使用 B+ Tree 且支持事务，后者使用 LSM 且不支持事务**。

其实 bolt 和 bbolt 基本没太大差异，为求统一，下文均用 bbolt 来唯一标示同类项目。

### 尝鲜使用
以下这个例子是一个完整的测试例子。可能有一些概念读者暂时还不清楚，不过没有关系，可以先运行起来观察现象，然后结合后续几个章节来理解这个简单的例子。

如下所示：
```go
package main

import (
	"fmt"
	"log"
	"time"

	bolt "go.etcd.io/bbolt"
)

var testBucket = []byte("test-bucket")

func main() {
	// 在当前目录下打开 my.db 这个文件
	// 如果文件不存在，将会自动创建
	db, err := bolt.Open("my.db", 0600, &bolt.Options{Timeout: 1 * time.Second})
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	key := []byte("hello")
	value := []byte("world")

	// 创建一个 read-write 事务来进行写操作
	err = db.Update(func(tx *bolt.Tx) error {
		// 如果 bucket 不存在则，创建一个 bucket
		bucket, err := tx.CreateBucketIfNotExists(testBucket)
		if err != nil {
			return err
		}

		// 将 key-value 写入到 bucket 中
		err = bucket.Put(key, value)
		if err != nil {
			return err
		}
		return nil
	})
	if err != nil {
		log.Fatal(err)
	}

	// 创建一个 read-only 事务来获取数据
	err = db.View(func(tx *bolt.Tx) error {
		// 获取对应的 bucket
		bucket := tx.Bucket(testBucket)
		// 如果 bucket 返回为 nil，则说明不存在对应 bucket
		if bucket == nil {
			return fmt.Errorf("Bucket %q is not found", testBucket)
		}
		// 从 bucket 中获取对应的 key（即上面写入的 key-value）
		val := bucket.Get(key)
		fmt.Printf("%s: %s\n", string(key), string(val))
		return nil
	})
	if err != nil {
		log.Fatal(err)
	}
}
```
运行例子：
```s
$ go run main.go 
hello: world
```

### bbolt 的数据模型
bbolt 存储的数据模型非常简单，就是单纯二进制的 key 和 value，即 Go 中 `[]byte` 类型的数据。

`bbolt 的数据由多个 bucket 组成，每个 bucket 是由多个 kv 对组成`，也就是说，bbolt 数据库是 bucket 的组合，而 bucket 是 kv 的组合。`bucket 内中的所有 key 必须唯一`。每个 key 在 bucket 中都是 byte-sorted，即 key 是有序存储的。

当我们需要操作一个 kv 的时候，必须创建或者获取一个 bucket，后续的操作均基于这个 bucket 对象进行，如下所示：
```go
bucket, err := tx.CreateBucketIfNotExists([]byte("test"))
if err != nil {
        return err
}

err = bucket.Put(key, value)
if err != nil {
        return err
}
```
#### 对 bucket 的主要操作
* 创建 bucket
    ```go
    b, err := tc.CreateBucket([]byte("MyBucket"))
    if err != nil {
        ...
    }
    ```
也可以使用 `CreateBucketIfNotExists()`，当 bucket 不存在的时候进行创建动作。
* 删除 bucket，直接使用 `Tx.DeleteBucket()`
* 获取对应的 bucket，直接使用 `Tx.Bucket()`，如下
    ```go
    b := tx.Bucket([]byte("MyBucket"))
    ```
#### 对 key-value 的主要操作
对 kv 的操作必须建立在已经获取 bucket 的基础上：
* 读写 kv
```go
v := b.Get([]byte("answer"))
err := b.Put([]byte("answer"), []byte("42"))
if err != nil {
        ...
}
```
* 删除 kv，使用 b.Delete() 即可；

### bbolt 的事务模型
#### 读写事务和只读事务
bbolt 只允许同一时间只有一个 read-write 事务，但可以有多个 read-only 事务。

启动一个 read-write 事务：
```go
err := db.Update(func(tx *bolt.Tx) error {
        ...
})

if err != nil {
        ...
}
```
启动一个 read-only 事务：
```go
err := db.View(func(tx *bolt.Tx) error {
        ...
})

if err != nil {
        ...
}
```
从这两种风格可以看到，在 bbolt 中，对 db 的读写都是以 lambda 函数的形式进行（这一点第一次使用的时候有点不是很适应）。要注意一点的是，**读取到 key 和 value 的生命周期只能维持在当前事务中，所以如果要使用这些值，必须将其复制出来**。

**绝大多数对 bbolt 的使用都是使用 `db.Update()` 和 `db.View()`**。
#### 批量写事务
每一个写事务的完成都必须等待落盘完成，如果每一个事务都一次写盘，这无疑将造成较大的 IO 压力，因此可用 batch 的方式来优化。bbolt 提供了以 `db.Batch()` 来实现批量的事务提交。

所有使用 `db.Update()` 的地方都可以等价替换成 `db.Batch()`，但是底层实现将会把多个事务合并成一个更大的失误。

使用 `db.Batch()` 将造成如下副作用：**传入的 function 可能因部分事务失败而被重复执行，从而要求 function 必须要符合幂等性**。

#### 手动控制事务
bbolt 的事务可以让用户来手动操控，只需要：

1. 调用 db.Begin() 创建事务 tx，并且 defer tx.Rollback()；
2. 开始进行操作；
3. 调用 tx.Commit() 提交事务；
比如我们可以这样开启一个 read-write 事务：

```go
// true 表示可写，如果是只读事务，则设置为 false
tx, err := db.Begin(true)
if err != nil {
    return err
}
defer tx.Rollback()

// Use the transaction...
_, err := tx.CreateBucket([]byte("MyBucket"))
if err != nil {
    return err
}

// Commit the transaction and check for error.
if err := tx.Commit(); err != nil {
    return err
}
```
在 etcd 的实现中，事务是手动操作，并启动一个 goroutine 定期（默认 100ms）进行批量提交。
我们看一下 db.Update() 的实现（db.View() 的实现与其只有 writable 设置不一样而已）：

```go
func (db *DB) Update(fn func(*Tx) error) error {
	// 创建一个可写的事务
	t, err := db.Begin(true)
	if err != nil {
		return err
	}

	// 保证有意外发生时，一定会执行 rollback()
	// t.db != nil 说明当前 tx 没有正确 commit
	defer func() {
		if t.db != nil {
			t.rollback()
		}
	}()

	// 执行 commit() 的之后会检查该标志，只有 managed 为 false 才能提交事务
	// 这里设置为 true 来确保 fn 中不会执行 commit，如果执行将会失败
	t.managed = true

	// 执行 lambda 函数
	err = fn(t)

	t.managed = false
	if err != nil {
		// 如果 fn 执行发生了错误，执行 rollback() 并返回 error
		_ = t.Rollback()
		return err
	}

	// 提交事务
	return t.Commit()
}
```
看上去非常简单，只要用好 Go 提供的 defer 语义，就可以有效避免因事务失败而没有回滚事务的违背 ACID 的行为出现。
### bbolt 的使用
bbolt 是一个基于 Go 的库，所以使用者只需要引入对应的 package 即可使用，使用前下载对应的 package：
```go
$ go get -u go.etcd.io/bbolt/...
```
#### 打开数据库
可直接使用 bolt.Open() 函数：
```go
package main

import (
	"log"

	bolt "go.etcd.io/bbolt"
)

func main() {
	// 在当前目录下打开 my.db 这个文件
	// 如果文件不存在，将会自动创建
	// 最后一个参数是 option 字段
	db, err := bolt.Open("my.db", 0600, nil)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
}
```
如果当前目录没有 my.db，程序运行后将生成对应的文件。

**注意**：数据库文件默认是加锁访问，多个不同进程无法同时使用。如果有进程打开一个使用中的数据库，将会被 hang 住知道对应进程释放文件锁。如果不想无限期地等待，可在 bolt.Open() 中配置 timeout 参数：
```go
db, err := bolt.Open("my.db", 0600, &bolt.Options{Timeout: 1 * time.Second})
```
#### 遍历操作
每个 key 在 bucket 中都是有序存储，可以使用 Cursor() 方法开启迭代器模式：
```go
// 创建一个 read-write 事务来进行写操作
err = db.Update(func(tx *bolt.Tx) error {

    // 如果 bucket 不存在则，创建一个 bucket
	bucket, err := tx.CreateBucketIfNotExists(testBucket)
	if err != nil {
		return err
	}

	for i := 0; i < 10; i++ {
		// 将 key-value 写入到 bucket 中
		err = bucket.Put([]byte(string(key)+strconv.Itoa(i)), value)
		if err != nil {
			return err
		}
	}

	return nil
})

if err != nil {
	log.Fatal(err)
}

err = db.View(func(tx *bolt.Tx) error {
	b := tx.Bucket(testBucket)

	c := b.Cursor()

    // 启动遍历模式
	for k, v := c.First(); k != nil; k, v = c.Next() {
		fmt.Printf("key=%s, value=%s\n", string(k), string(v))
	}

	return nil
})

if err != nil {
	log.Fatal(err)
}
```
我们还可以通过 key 的前缀（prefix）来进行指定前缀 key 的遍历，如下：
```go
...
c := tx.Bucket([]byte("MyBucket")).Cursor()

prefix := []byte("hello")
for k, v := c.Seek(prefix); k != nil && bytes.HasPrefix(k, prefix); k, v = c.Next() {
    fmt.Printf("key=%s, value=%s\n", string(k), string(v))
}
...
```
使用 `Seek()` 是移动到指定的 key 上面。

由于 key 在 bbolt 内部的有序性，我们还在上面的基础上稍微改变一下遍历条件，就可以实现一个**基于范围的遍历（range scan）**:
```go
min := []byte("1990-01-01T00:00:00Z")
max := []byte("2000-01-01T00:00:00Z")

// Iterate over the 90's.
for k, v := c.Seek(min); k != nil && bytes.Compare(k, max) <= 0; k, v = c.Next() {
	fmt.Printf("%s: %s\n", k, v)
}
```
使用迭代器的遍历还有一种变种，即 `ForEach()`：
```go
db.View(func(tx *bolt.Tx) error {
	// Assume bucket exists and has keys
	b := tx.Bucket([]byte("MyBucket"))

	b.ForEach(func(k, v []byte) error {
		fmt.Printf("key=%s, value=%s\n", k, v)
		return nil
	})
	return nil
})
```
#### 其他特性
bbolt 还支持数据库的 backup、统计信息、只读模式等功能，此处就不一一列举，具体可参考项目地址。

### bbolt 的实现
bbolt 的实现非常简单，正如参考文档 3 所说的：「**BoltDB 源码相当清晰，没有黑魔法**」。确实，bbolt 只使用 B+ Tree 和一些基本的优化手段就实现了一个架构简洁的嵌入式数据库引擎，可谓功力深厚。如果不算测试代码和一些平台兼容性相关的代码，**整个 bbolt 的代码量约为 2700 行左右**，所以将其完整读完并不算一件特别困难的事情。etcd 基于 bbolt，写出来的代码也相当干净利落，看起来非常舒服，个人感觉比 Google 他们写的 Kubernetes 的代码要更「轻松些」，k8s 的代码读起来总有一种莫名的沉重感。
#### B+ Tree
B+ Tree 是一种对磁盘读友好的数据索引算法。目前大多数数据库主要以 B+ Tree（或者 B Tree，其实二者的差异很小）和 LSM 为索引的基础。比如 MySQL 就是用 B+ Tree 来创建索引，而 LevelDB 和 RocksDB 则是用 LSM（当然，这两种 DB 面向的问题域并不完全相同）。
#### 基于 mmap 来实现数据读写
bbolt 内部使用 mmap() 系统调用来实现数据的读写。bbolt 数据库是一个单文件数据，当打开一个数据库时，bbolt 将使用 mmap() 将该文件加锁打开并映射到进程的虚拟空间中，此时对文件的读写都可以直接操作进程的虚拟进程空间，而不需要使用 read() 和 write()。这样的好处是显而易见的：节省内核空间和用户空间的一次传输并且减少频繁的系统调用。
正常的 read() 和 write() 需要两次传输：
```s
用户缓冲区 <-> 内核缓冲区 <-> 文件
```
此时，**数据将被保存在两个缓冲区中**。

如果使用 mmap()，内核将相应的文件块映射进内存之后用户进程就直接可以使用这些数据，无需用户缓冲区和内核缓冲区之间的拷贝，此时，内核空间和用户空间将共享同一个缓冲区。如果有多个进程正在同一个文件上执行 I/O，那么此时多个进程通过 mmap() 来共享同一个内核缓冲区，理论上还可以节省一定的内存消耗。

但是，使用 mmap() 不是没有副作用的。使用 mmap() 会带来其他额外的开销，比如：映射的建立和销毁和因 page fault 而导致的页的换入换出等。如果顺序地访问一个文件，并假设执行 IO 所使用的缓冲区大小足够大以至于能够避免大量的 IO 相关的系统调用，这时候 mmap() 较之 read() 和 write() 性能提升有限。特别地，在小数据量的 IO 上，建立 mmap() 所带来的开销反而比单纯使用 read() 和 write() 要大。因此，mmap() 在大型文件中执行重复随机读写时最有可能体现出性能优势。但是，如果文件远超实际物理内存大小，使用 mmap() 将可能导致频繁的 page fault，此时带来的性能损耗将及为客观（内存页需要被换到磁盘上从而会带来一定的磁盘 IO）。

不过 bbolt 在使用 mmap() 用于文件读写上有其他优化手段，这一点我们下一篇文章再聊聊。

#### free list 结构
bbolt 使用单个文件进行 mmap()，并且在文件内部通过划分 page，采用 free list 的方式来进行管理。

### 用到的 Go 特性
bbolt 用 Go 实现，但也因为 Go 的简单性，代码中并没有太多奇技淫巧。我觉得值得注意的地方有以下几点：
* 用 goroutine 实现一些异步逻辑（比如 batch 事务提交和事务检查）；

    比如在 db.go 中，当 batch 的调用达到一定阈值时，将启动一个 goroutine 来执行 trigger 动作
    ```go
        if len(db.batch.calls) >= db.MaxBatchSize {
            // wake up batch, it's ready to run
            go db.batch.trigger()
        }
    ```
    在 tx.go 中，也会启动 goroutine 来进行一些一致性检查的逻辑：
    ```go
        func (tx *Tx) Check() <-chan error {
            ch := make(chan error)
            go tx.check(ch)
            return ch
        }
    ```
* 使用 unsafe 包来实现一些底层的指针操作；
bbolt 在处理一些结构体的时候，必须做一些底层的指针转换，免不了必须使用 unsafe 包。统计了一下，bbolt 只用了 unsafe 的三个接口：unsafe.Pointer()、unsafe.Offsetof() 和unsafe.Sizeof()。如果不熟悉的话，可以参考以下这个简单的小例子：
```go
 package main

  import (
    "fmt"
    "unsafe"
  )

  type user struct {
    sex   string
    age   int
    money float64
  }

  func main() {
    // 创建一个 user 变量
    var u = user{
      sex:   "male",
      age:   10,
      money: 10.03,
    }

    // unsafe.Sizeof() 用于获取一个结构体的大小
    fmt.Printf("sizeof(user): %d\n", unsafe.Sizeof(u))
    fmt.Printf("before modified: user: %v\n", u)

    // pUser 是一个 *User 类型指针
    pUser := &u

    // pi 是一个 *int 类型指针
    // unsafe.Offsetof 用于获取结构体中对应字段的偏移量
    // unsafe.Pointer() 用于将转换指针，类似于 C 语言的 void*
    // 将 pUser 通过 unsafe.Pointer 转换后再转换成 uintptr 用于进行指针的加法，然后再转换为 *int 类型
    pi := (*int)(unsafe.Pointer(uintptr(unsafe.Pointer(pUser)) + unsafe.Offsetof(u.age)))
    *pi = *pi * 3

    // 此时可以观察到 user.age 字段较之前的输出有了变化
    fmt.Printf("after modified: user: %v\n", u)
  }
```
### 参考文档

1. [Bolt — an embedded key/value database for Go](https://www.progville.com/go/bolt-embedded-db-golang/)；

2. [Intro to BoltDB: Painless Performant Persistence](https://npf.io/2014/07/intro-to-boltdb-painless-performant-persistence/)；

3. [BoltDB 的优点与缺点](https://zhuanlan.zhihu.com/p/47214093)；

4. [boltdb 源码分析](https://lrita.github.io/2017/05/21/boltdb-overview-0/)


