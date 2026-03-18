// test_prog.java — intentionally slow bubble sort in Java
class test_prog {
    static void bubbleSort(int[] arr) {
        int n = arr.length;
        for (int i = 0; i < n - 1; i++)
            for (int j = 0; j < n - i - 1; j++)
                if (arr[j] > arr[j + 1]) {
                    int t = arr[j];
                    arr[j] = arr[j + 1];
                    arr[j + 1] = t;
                }
    }

    public static void main(String[] args) {
        int N = 8000;
        int[] arr = new int[N];
        for (int i = 0; i < N; i++) arr[i] = N - i;
        for (int rep = 0; rep < 5; rep++)
            bubbleSort(arr);
        System.out.printf("sorted[0]=%d sorted[%d]=%d%n", arr[0], N - 1, arr[N - 1]);
    }
}
