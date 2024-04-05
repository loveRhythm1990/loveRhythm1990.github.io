---
layout:     post
title:      "理解数据库事务 ACID"
date:       2024-04-03 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 数据库
---

**目录**
- [原子性（Atomicity）](#原子性atomicity)
- [一致性（Consistency）](#一致性consistency)
- [隔离性（Isolation）](#隔离性isolation)
  - [read uncommit(读未提交)](#read-uncommit读未提交)
  - [read commit(读已提交)](#read-commit读已提交)
  - [repeatable read(可重复度)](#repeatable-read可重复度)
  - [serializable(串行化)](#serializable串行化)
- [持久性（Durability）](#持久性durability)

## 原子性（Atomicity）
一组操作要么同时成功，要么同时失败，通过 undo 操作来保证。比如一个事务有两个步骤a、b，如果 b 步骤失败了，则要把 a 步骤的操作都要撤销，如把 a 步骤插入的数据删除。

## 一致性（Consistency）
事务的一致性是指在事务前后，数据必须是保持正确并遵守所有数据相关的约束。参考 [通用事务概念](https://docs.matrixorigin.cn/1.1.2/MatrixOne/Develop/Transactions/common-transaction-overview/) 中的例子，例如，我们在数据库中建立一个新表
```sql
create table t1(a int primary key,b varchar(5) not null);
```
此处为了确保数据一致性，我们在插入数据时，你要保证 a 和 b 列的数据类型与范围，同时还要满足 a 列的主键约束与 b 列的非空约束：
```sql
insert into t1 values(1,'abcde'),(2,'bcdef');
```

关于一致性的理解还可以参考[如何理解数据库事务中的一致性的概念？](https://www.zhihu.com/question/31346392):
> ACID里的AID都是数据库的特征,也就是依赖数据库的具体实现.而唯独这个C,实际上它依赖于应用层,也就是依赖于开发者.这里的一致性是指系统从一个正确的状态,迁移到另一个正确的状态.什么叫正确的状态呢?就是当前的状态满足预定的约束就叫做正确的状态.而事务具备ACID里C的特性是说通过事务的AID来保证我们的一致性.

## 隔离性（Isolation）
事务并发执行时，内部的操作不能相互干扰，innoDB 引擎中，定义了四种隔离级别供我们使用，级别越高隔离性越好，但性能就越低，而隔离性是由 mysql 中各种锁以及 MVCC 机制来实现的。在 mysql 中可以通过 `set tx_isolation='read-uncommitted'` 来配置隔离级别。

> cow(copy on write)，写的时候，把整个原数据做一个副本，然后去修改这个副本，等修改完了用这个副本替换整个原数据。在写副本的过程中，其他线程仍然读原数据（即有可能是读旧数据，但不是~~脏~~数据，读旧数据一般是能容忍的），这样读和写是同时执行的，实现读写高并发。也叫读写分离。

### read uncommit(读未提交)
可以从字面意思上理解，就是事务 a 未提交的数据，可以被事务 b 读到。但是 a 提交的数据可能会因为 undo 被撤销，所以读未提交有 **脏读** 问题。这种隔离级别几乎没有人在用。
### read commit(读已提交)
只有数据被提交之后，才能被读到。此隔离级别有一个问题是 **不可重复读**，假设 b 事务在读数据，并且对同一个数据读了多次，那可能对于这同一个数据可能每次读出不同的结果，因为这个数据被 a 事务或者其他事务提交了多次。
### repeatable read(可重复度)
在同一个事务中，每次读一个特定数据时，不管读几次，都把第一次都到的数据作为结果。虽然这个可能又被其他事务给提交了。下面有两个事务 a,b。a 对一个 id 为1的数据更新了两次，假设在 a 第一次修改commit之后，b事务读了这个数据，那不管后面这个数据又被修改了几次，在同一个事务中，读到的数据始终是一样的，即是第一次读到的 commit 的数据。。

a 事务
```sql
set tx_isolation='reatable-read';
BEGIN;
update account SET balance=balance+500 where id=1;
COMMIT;
-- 第一次修改commit之后，b事务进行了查询操作。
BEGIN; -- 第二次的修改，b事务看不到
update account SET balance=balance+800 where id=1;
COMMIT;
```
b 事务
```sql
set tx_isolation='repeatable-read';
BEGIN;
select * from account where id=1; -- 在a事务第一次commit之后读数据
select * from account where id=1; -- 结果跟上面一致，可能是旧数据
select * from account where id=1;
COMMIT;
```
可重复度是在事务第一次都数据时，读的**整个数据库**的一个快照，所以在读其他行、其他表的时候，有可能读的是陈旧的数据，也就是快照数据，这就是可重复读中的 **幻读**问题，mysql 默认的隔离级别是可重复度 rr。
### serializable(串行化)
a事务在修改数据时，b事务读的时候会卡在，会等着a。只有a提交了之后，b事务才能读到结果（同样，如果a发现在b在读数据，只有b commit结束之后，a才能更新）。这个性能比较差，用的不多。在执行 sql 语句时，通过在语句后面加上`lock in share mode`即可实现串行化效果（读锁），如下，虽然设置的隔离级别是读未提交。
```sql
set tx_isolation='read-uncommitted'
BEGIN;
select * from account where id=1 lock in share mode;
COMMIT;
```
串行化实现原理就是底层加了读锁。


## 持久性（Durability）
一旦提交了事务，它对数据库的改变就应该是永久性的。持久性 redo log日志来保证。