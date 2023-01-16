---
layout:     post
title:      "环形链表"
date:       2020-2-21 17:06:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 数据结构/算法
---

leetcode题目：

[环形链表](https://leetcode-cn.com/problems/linked-list-cycle/)

[环形链表ii](https://leetcode.com/problems/linked-list-cycle-ii/)

第一个是判断链表中是否有环，第二个是寻找环的入口，这种题目还是记录一下吧，不然每次想不起来都要翻网页太麻烦了。

### 判断链表中是否有环
设置快慢指针fast,slow，fast每次走两步，slow每次走一步，如果链表没有环，fast肯定先遇到nil，如果链表有环，那么fast跟slow肯定是会相遇的，并且是在环内相遇（fast进入环之后就会一直兜圈子，当然不可能在环外相遇）
，因为每走1轮，fast与slow的间距+1，fast终会追上slow。代码如下：
```go
func hasCycle(head *ListNode) bool {
	fast, slow := head, head
	for fast != nil && fast.Next != nil {
		fast = fast.Next.Next
		slow = slow.Next

		if slow == fast {
			return true
		}
	}
	return false
}
```

### 寻找环的入口
假设相遇时slow走了s步，则fast走了2s步，假设环的长度为r，那么相遇时，fast一定比slow多兜了n个圈子，即：

`2s = s + nr`

并由此推导出：

`s = nr`

这个结论是，相遇时，slow走的步数正好是n个圈子。

我们假设从链表开始到环入口的距离是a，那么slow每次走到圈入口时，走的步数为a+kr，其中k=0,1,2,3...
即：
我走a步时会走到圈入口

我走a步，并多走一个圈子时会走到圈入口

我走a步，并多走2个全资时会走到圈入口

我走a步，并多走3个全资时会走到圈入口

......

以此类推。

我们上面得出一个结论是，相遇时，slow走了nr个圈子，那么只要再多走a步，就能走到圈入口了。

但是我们并不知道a是多少，这时候我们可以借助双指针，因为我们知道从开头走到入口时也是a步，那么从p指针从像雨点开始走，q指针从链表头开始走，那么他们走a步就会相等，也就是在圈入口相遇，代码为：
```go
func detectCycle(head *ListNode) *ListNode {

	var meet *ListNode

	fast, slow := head, head
	for fast != nil && fast.Next != nil {
		fast = fast.Next.Next
		slow = slow.Next

		if slow == fast {
			meet = slow
			break
		}
	}
	if fast == nil || fast.Next == nil {
		return nil
	}

	for head != meet {
		head = head.Next
		meet = meet.Next
	}
	return head
}
```

