// test_prog.cpp — intentionally slow bubble sort in C++
#include <cstdio>
#include <vector>
#include <string>

constexpr int N = 8000;

void bubble_sort(std::vector<int>& arr) {
    int n = static_cast<int>(arr.size());
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - i - 1; j++)
            if (arr[j] > arr[j + 1])
                std::swap(arr[j], arr[j + 1]);
}

int main() {
    std::vector<int> arr(N);
    for (int i = 0; i < N; i++) arr[i] = N - i;
    for (int rep = 0; rep < 5; rep++)
        bubble_sort(arr);
    std::printf("sorted[0]=%d sorted[%d]=%d\n", arr[0], N - 1, arr[N - 1]);
    return 0;
}
