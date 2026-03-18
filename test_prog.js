// test_prog.js — intentionally slow bubble sort in Node.js
const N = 6000;

function bubbleSort(arr) {
    const n = arr.length;
    for (let i = 0; i < n - 1; i++) {
        for (let j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                const t = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = t;
            }
        }
    }
}

function main() {
    const arr = Array.from({ length: N }, (_, i) => N - i);
    for (let rep = 0; rep < 3; rep++) {
        bubbleSort(arr);
    }
    console.log(`sorted[0]=${arr[0]} sorted[${N - 1}]=${arr[N - 1]}`);
}

main();
