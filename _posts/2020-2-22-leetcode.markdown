---
layout:     post
title:      "leetcode题解"
subtitle:   " \"持续更新...\""
date:       2020-2-22 22:06:00
author:     "weak old dog"
header-img-credit: false
tags:
    - leetcode
---

### [33. 搜索旋转排序数组](https://leetcode-cn.com/problems/search-in-rotated-sorted-array/)

假设按照升序排序的数组在预先未知的某个点上进行了旋转。

( 例如，数组 [0,1,2,4,5,6,7] 可能变为 [4,5,6,7,0,1,2] )。

搜索一个给定的目标值，如果数组中存在这个目标值，则返回它的索引，否则返回 -1 。

你可以假设数组中不存在重复的元素。
你的算法时间复杂度必须是 O(log n) 级别。

示例 1:
输入: nums = [4,5,6,7,0,1,2], target = 0   输出: 4

示例 2:
输入: nums = [4,5,6,7,0,1,2], target = 3   输出: -1

这个题关键在于找确定关系，本题的确定关系为:`给定任何一个数组的下标，下标的左右两侧中，一定有一侧是有序的`，可以自己画个图看看。有了这个确定关系，我们在决定向左找还是向右找的时候，只要在有序的那边看一下target是否在那边，这个很好确定，因为其是有序的，我们知道最大值最小值，比较一下就可以了。

那有个问题是如何判断那边是有序的，其实只要跟两边的值比较一下就可以了，假设左边是l，右边是h，中间是m，那么如果：nums[m] > nums[l]，那么左边是有序的，如果nums[m] < nums[l]，那么右边是有序的。同样的，跟右边的值比较也是可以的：如果nums[m] < nums[h]那么右边是有序的，如果nums[m] > nums[h]，那么左边是有序的。但是对于二分查找有个问题，那就是m是有可能等于l的，即下标相同，这种情况下，对于`[2, 1]target=1`这种case，比较难处理（不容易理解）。所以跟右边比较好。贴出两种方式的代码。

跟右边比较：
```go
func search(nums []int, target int) int {
	l, h := 0, len(nums)-1

	for l <= h {
		m := (l+h)/2
		if nums[m] == target {
			return m
		}

		if nums[h] < nums[m] { // left is ordered
			if nums[m] > target && target >= nums[l] {
				h = m - 1
			} else {
				l = m + 1
			}
		} else { // right is ordered
			if nums[m] < target && nums[h] >= target {
				l = m + 1
			} else {
				h = m - 1
			}
		}
	}
	return -1
}
```
跟左边比较：
```go
func search2(nums []int, target int) int {
	l, h := 0, len(nums)-1

	for l <= h {
		m := (l+h)/2
		if nums[m] == target {
			return m
		}

		if nums[l] <= nums[m] { // left is ordered
			if nums[m] > target && target >= nums[l] {
				h = m - 1
			} else {
				l = m + 1
			}
		} else { // right is ordered
			if nums[m] < target && nums[h] >= target {
				l = m + 1
			} else {
				h = m - 1
			}
		}
	}
	return -1
}
```
