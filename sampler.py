import os
import csv
import argparse

def sample_csv(input_path, n=6):
    if not os.path.isfile(input_path):
        print(f"ERROR: File not found: {input_path}")
        return

    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_sample{ext or '.csv'}"

    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        print(f"ERROR: '{input_path}' is empty.")
        return

    header, data = rows[0], rows[1:]
    sampled = data[:n]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(sampled)

    print(f"Input : {input_path} ({len(data)} data rows)")
    print(f"Output: {output_path} ({len(sampled)} data rows + header)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Take the first N data rows of a CSV into a *_sample.csv file.")
    parser.add_argument("input", help="Path to the source CSV")
    parser.add_argument("-n", type=int, default=6, help="Number of data rows to sample (default: 6)")
    args = parser.parse_args()

    sample_csv(args.input, args.n)
