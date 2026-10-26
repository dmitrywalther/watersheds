// Compile with the Windows .NET Framework compiler, /unsafe /platform:x64 /optimize+.
// Raw input: row-major ESRI D8 bytes, 0 terminal, 255 NoData.
// Raw output: little-endian uint32 counts INCLUDING each valid cell itself.
// State: 0..8 pending indegree, 254 NoData, 255 processed.
using System;
using System.Globalization;
using System.IO;
using System.IO.MemoryMappedFiles;

internal static unsafe class FlowAccumulation
{
    private const byte NoData = 255;
    private const byte InvalidState = 254;
    private const byte ProcessedState = 255;
    private static readonly int[] RowOffsets = { 0, 1, 1, 1, 0, -1, -1, -1 };
    private static readonly int[] ColOffsets = { 1, 1, 0, -1, -1, -1, 0, 1 };
    private static readonly byte[] InwardCodes = { 16, 32, 64, 128, 1, 2, 4, 8 };

    private sealed class MappedBytes : IDisposable
    {
        private FileStream stream;
        private MemoryMappedFile mapping;
        private MemoryMappedViewAccessor view;
        private bool acquired;
        public byte* Data;

        public MappedBytes(string path, long length, bool output)
        {
            try
            {
                stream = new FileStream(path, output ? FileMode.CreateNew : FileMode.Open,
                    output ? FileAccess.ReadWrite : FileAccess.Read,
                    output ? FileShare.None : FileShare.Read);
                if (output)
                    stream.SetLength(length);
                else if (stream.Length != length)
                    throw new InvalidDataException("Direction file length does not match rows * cols.");
                MemoryMappedFileAccess access = output ? MemoryMappedFileAccess.ReadWrite : MemoryMappedFileAccess.Read;
                mapping = MemoryMappedFile.CreateFromFile(stream, null, length, access,
                    HandleInheritability.None, true);
                view = mapping.CreateViewAccessor(0, length, access);
                byte* pointer = null;
                view.SafeMemoryMappedViewHandle.AcquirePointer(ref pointer);
                acquired = true;
                Data = pointer; // The mapped view starts at file offset zero.
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        public void Flush()
        {
            view.Flush();
            stream.Flush(true);
        }

        public void Dispose()
        {
            if (acquired)
            {
                view.SafeMemoryMappedViewHandle.ReleasePointer();
                acquired = false;
                Data = null;
            }
            if (view != null) { view.Dispose(); view = null; }
            if (mapping != null) { mapping.Dispose(); mapping = null; }
            if (stream != null) { stream.Dispose(); stream = null; }
        }
    }

    private static void Report(string phase, long rowsDone, long rows, long cols,
                               long cells, long valid, long processed)
    {
        Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"phase\":\"{0}\",\"rows_done\":{1},\"rows\":{2},\"cols\":{3},\"cells\":{4},\"valid\":{5},\"processed\":{6}}}",
            phase, rowsDone, rows, cols, cells, valid, processed));
        Console.Out.Flush();
    }

    private static bool IsDirection(byte value)
    {
        return value == 0 || value == NoData || (value & (value - 1)) == 0;
    }

    private static long Destination(long index, byte direction, long rows, long cols)
    {
        int dr, dc;
        switch (direction)
        {
            case 1: dr = 0; dc = 1; break;
            case 2: dr = 1; dc = 1; break;
            case 4: dr = 1; dc = 0; break;
            case 8: dr = 1; dc = -1; break;
            case 16: dr = 0; dc = -1; break;
            case 32: dr = -1; dc = -1; break;
            case 64: dr = -1; dc = 0; break;
            case 128: dr = -1; dc = 1; break;
            default: return -1;
        }
        long row = index / cols + dr;
        long col = index % cols + dc;
        if (row < 0 || row >= rows || col < 0 || col >= cols)
            return -1;
        return row * cols + col;
    }

    private static void Accumulate(string directionPath, string countPath, string statePath,
                                   long rows, long cols, long cells)
    {
        long valid = 0, processed = 0;
        Report("initialize", 0, rows, cols, cells, valid, processed);
        using (MappedBytes directions = new MappedBytes(directionPath, cells, false))
        using (MappedBytes countsFile = new MappedBytes(countPath, checked(cells * 4), true))
        using (MappedBytes stateFile = new MappedBytes(statePath, cells, true))
        {
            byte* dirs = directions.Data;
            byte* state = stateFile.Data;
            uint* counts = (uint*)countsFile.Data;
            for (long row = 0; row < rows; row++)
            {
                long start = row * cols;
                for (long col = 0; col < cols; col++)
                {
                    long index = start + col;
                    byte direction = dirs[index];
                    if (!IsDirection(direction))
                        throw new InvalidDataException(string.Format(CultureInfo.InvariantCulture,
                            "Invalid direction code {0} at row {1}, col {2}.", direction, row, col));
                    if (direction == NoData)
                    {
                        state[index] = InvalidState;
                        counts[index] = 0;
                        continue;
                    }
                    valid++;
                    counts[index] = 1;
                    byte degree = 0;
                    for (int neighbor = 0; neighbor < 8; neighbor++)
                    {
                        long rr = row + RowOffsets[neighbor];
                        long cc = col + ColOffsets[neighbor];
                        if (rr >= 0 && rr < rows && cc >= 0 && cc < cols &&
                            dirs[rr * cols + cc] == InwardCodes[neighbor])
                            degree++;
                    }
                    state[index] = degree;
                }
                if ((row + 1) % 1024 == 0 || row + 1 == rows)
                    Report("initialize", row + 1, rows, cols, cells, valid, processed);
            }

            Report("accumulate", 0, rows, cols, cells, valid, processed);
            for (long row = 0; row < rows; row++)
            {
                long start = row * cols;
                for (long col = 0; col < cols; col++)
                {
                    long current = start + col;
                    // Walking newly ready receivers also processes cells already passed by the scan.
                    // Every cell becomes ready at most once, so no queue or stack is required.
                    while (state[current] == 0)
                    {
                        state[current] = ProcessedState;
                        processed++;
                        long destination = Destination(current, dirs[current], rows, cols);
                        if (destination < 0 || dirs[destination] == NoData)
                            break;
                        if (state[destination] == ProcessedState || state[destination] == 0)
                            throw new InvalidDataException("Inconsistent downstream indegree.");
                        counts[destination] = checked(counts[destination] + counts[current]);
                        state[destination]--;
                        if (state[destination] != 0)
                            break;
                        current = destination;
                    }
                }
                if ((row + 1) % 1024 == 0 || row + 1 == rows)
                    Report("accumulate", row + 1, rows, cols, cells, valid, processed);
            }
            if (processed != valid)
                throw new InvalidDataException(string.Format(CultureInfo.InvariantCulture,
                    "Direction cycle detected: {0} of {1} valid cells remain unresolved.", valid - processed, valid));
            countsFile.Flush();
            stateFile.Flush();
        }
        Report("complete", rows, rows, cols, cells, valid, processed);
    }

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length != 5)
                throw new ArgumentException("Usage: FlowAccumulation directions.raw counts.raw state.raw rows cols");
            if (IntPtr.Size != 8 || !BitConverter.IsLittleEndian)
                throw new PlatformNotSupportedException("Requires a little-endian 64-bit process.");
            long rows = long.Parse(args[3], CultureInfo.InvariantCulture);
            long cols = long.Parse(args[4], CultureInfo.InvariantCulture);
            if (rows <= 0 || cols <= 0)
                throw new ArgumentException("Rows and columns must be positive.");
            long cells = checked(rows * cols);
            if (cells > uint.MaxValue)
                throw new ArgumentException("Cell count exceeds the uint32 accumulation limit.");
            string direction = Path.GetFullPath(args[0]);
            string counts = Path.GetFullPath(args[1]);
            string state = Path.GetFullPath(args[2]);
            if (string.Equals(direction, counts, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(direction, state, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(counts, state, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Input and output paths must all be distinct.");
            Accumulate(direction, counts, state, rows, cols, cells);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            Console.Error.Flush();
            return 1;
        }
    }
}
