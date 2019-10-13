---
layout:     post
title:      "回溯法合集"
subtitle:   " \"关于回溯法题目的总结\""
date:       2019-10-13 16:09:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 算法
---

> 源于leetcode上的一些题目，有时候解决不掉倍感痛苦，想看别人的解题思路，奈何思维方式不同，强搬硬套更痛苦，又记不住，这里打算自己总结一下遇到的使用回溯法的典型题目。尤其是，有些题目自己当时做出来了，但是转眼间就忘了，甚至惊讶当时自己怎么做出来的。另外自己之前也写过一点回溯法的东西，但是现在去看也是有点懵，但是并没有好好总结，浅尝辄止，这里希望这篇文章能一直被修改更新（毕竟离职也是一直存在的），争取攻克回溯难题。

### 全排列
全排列这种题目，自己做过很多次了，然而每次做都不能痛快的写出来，很痛苦。leetcode上关于全排列的题目为（现在用力扣多一点）：

46. [全排列](https://leetcode-cn.com/problems/permutations/)，不含相同字符的全排列。
47. [全排列 II](https://leetcode-cn.com/problems/permutations-ii/)，含相同字符的全排列，需要**去重**

网上关于用回溯法解决全排列的解法很多，自己也用过好几种解法，然而看的多了就茫然起来，每次都是跟着别人的思路走，后来发现自己的想法也不错，以后总结自己的套路。这里以47题为例，46题相对于47题改动很小。

`回溯法`，这里有必要考虑一下递推公式，假设有ABCD四个字符，我们用per(ABCD)表示由ABCD组成的全排列，那么递推公式可以写为：

pre(ABCD) = { A+pre(BCD), B+pre(ACD), C+pre(ABD), D+pre(ABC) }

也即：我们在求全排列时，首先`固定`处在第一个位置的字符，然后这个字符`拼接`剩下的字符组成的全排列。

`关于去重`：我们只要保证放在第一个位置的元素每次都是不同的，自然就去重了，这样是没毛病的，以pre{1122}为例，pre{1122}={ 1+pre{122}, 2+pre{112} }，只包括后面两种情况了，没有第三种情况了。

具体代码为：
```go
func permuteUnique(nums []int) [][]int {

	res := make([][]int, 0)
	if len(nums) == 1 {
		res = append(res, nums)
	}

	// 用于保证每次放在第一个位置的元素都不相同
	set := map[int]struct{}{}

	for i:=0; i<len(nums); i++{

		// 看看 nums[i] 是否曾经放在第一个位置过了
		if _, ok := set[nums[i]]; ok {
			continue
		}

		// 做深拷贝，让这个位置交换，只对他的字串可见
		acopy := make([]int, len(nums))
		copy(acopy, nums)
		acopy[0], acopy[i] = acopy[i], acopy[0]
		set[acopy[0]] = struct{}{}

		// 拼接第一个字串以及他的后面的字串组成的全排列
		pres := permuteUnique(acopy[1:])
		for _, r := range pres {
			ar := []int{}
			ar = append(ar, acopy[0])
			ar = append(ar, r...)
			res = append(res, ar)
		}
	}
	return res
}
```

### 组合总和
给定一个正整数数组candidates和一个目标数target（也是正整数），找出candidates中所有可以使数字和为target的组合。
> 解集不能包含重复的组合

39. [组合总和](https://leetcode-cn.com/problems/combination-sum/)，candidates无重复元素，数字可以使用无限次。

40. [组合总和 II](https://leetcode-cn.com/problems/combination-sum-ii/)，candidates含重复元素，要求candidates 中的每个数字在每个组合中只能使用一次。

我们首先看第一个题目`39题`，要按照一种方式找出**所有可能**，一开始照例没有思路。我在想要首先能有笔算的思路，然后把笔算的思路转换为代码，然后按照例子写写划划。题目给了如下一个例子：
```
输入: candidates = [2,6,3,7], target = 7,
所求解集为:
[
  [7],
  [2,2,3]
]
```
因为题目对输出的顺序，以及每个解的顺序都没有要求，所以首先不考虑顺序的问题。那么问题是如果给你这些数，如何按照一种方式找出所有能凑齐target的数字组合呢？先开始凑，~~发现如果是排过序的，而且是逆序的，那么问题简单一些~~：每个数a可以取0-N个，`a * N <= target`，那么不够的可以通过后面的补齐，注意**不够的可以通过后面的补齐**，递归来了，那么递归表达式怎么写呢？如下：
```go
combinationSum([a,b,c,d], target) = a + combinationSum([b,c,d], target-a)
                            以及    a + a +  combinationSum([b,c,d], target-a-a)
                                    ...
```
那么代码就好些了，刚开始以为要排序的，后来一想，不用排序，每个都可以选或者不选，关于去重，因为每个位置的数字都是选0个或者N个不同的可能，每个数又都是不同的，所以自然去重，不需要额外处理，关于39题的代码就不贴了。

再看`40题`。40题递归思路跟39题是一样的，有两点要求：每个数字在每个组合中最多使用一次，这个好说，把上面的0-N改成0-1就好了；`去重`，这个题的关键是去重，先说去重的方法吧，就是下面一个if语句：
```go
if i!=0 && candidates[i]==candidates[i-1] {
    continue
}
```
稍微解释下：每个for循环，都是以当前数字为开头（开头是指组合的第一个数）找一个序列，如果对于当前的target，前面的数已经找过了，这个就不用找了。也就是我们固定第一个数不同，然后从后面的字串继续找。若candidates[i-1]跟candidates[i]相等，它递归到i时，target已经变成之前的target-candidates[i-1]了，它还可以继续使用i后面的数。对candidates[i]来说，以它为第一个数时，这个过程是重复的。大家可以以`combinationSum2([2,2,1], 3)`为例分析这个情况。

```go
// 所有可能性
func combinationSum2(candidates []int, target int) [][]int {
	res := make([][]int, 0)

	// 降序排列，为了去重
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i] > candidates[j]
	})

	for i:=0; i<len(candidates); i++ {

		// 这个if语句去重
		if i!=0 && candidates[i]==candidates[i-1] {
			continue
		}
		if candidates[i] == target {
			res = append(res, []int{candidates[i]})
		}

		// start a loop must contain candidates[i]
		if candidates[i] < target {
			subArrays := combinationSum2(candidates[i+1:], target-candidates[i])
			for _, r := range subArrays {
				aaRes := []int{}
				aaRes = append(aaRes, candidates[i])
				aaRes = append(aaRes, r...)
				res = append(res, aaRes)
			}
		}
	}
	return res
}
```