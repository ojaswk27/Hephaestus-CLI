// test_prog.go — intentionally slow bubble sort in Go
package main

import "fmt"

const N = 8000

func bubbleSort(arr []int) {
	n := len(arr)
	for i := 0; i < n-1; i++ {
		for j := 0; j < n-i-1; j++ {
			if arr[j] > arr[j+1] {
				arr[j], arr[j+1] = arr[j+1], arr[j]
			}
		}
	}
}

func main() {
	arr := make([]int, N)
	for i := range arr {
		arr[i] = N - i
	}
	for rep := 0; rep < 5; rep++ {
		bubbleSort(arr)
	}
	fmt.Printf("sorted[0]=%d sorted[%d]=%d\n", arr[0], N-1, arr[N-1])
}
