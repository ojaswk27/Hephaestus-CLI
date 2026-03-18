/* test_prog.c — intentionally slow bubble sort */
#include <stdio.h>
#include <stdlib.h>

#define N 8000

void bubble_sort(int *arr, int n) {
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - i - 1; j++)
            if (arr[j] > arr[j + 1]) {
                int t = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = t;
            }
}

int main(void) {
    int *arr = malloc(N * sizeof(int));
    for (int i = 0; i < N; i++) arr[i] = N - i;
    for (int rep = 0; rep < 5; rep++)
        bubble_sort(arr, N);
    printf("sorted[0]=%d sorted[%d]=%d\n", arr[0], N - 1, arr[N - 1]);
    free(arr);
    return 0;
}
