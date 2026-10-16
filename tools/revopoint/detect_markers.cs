using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;

internal static class MarkerDetector
{
    private sealed class Component
    {
        public int Area;
        public int MinX = Int32.MaxValue;
        public int MinY = Int32.MaxValue;
        public int MaxX = Int32.MinValue;
        public int MaxY = Int32.MinValue;
        public int Perimeter;
        public int MaxGray;
        public long SumGray;
        public double WeightedX;
        public double WeightedY;
        public double Weight;

        public double CenterX { get { return WeightedX / Weight; } }
        public double CenterY { get { return WeightedY / Weight; } }
        public double Circularity { get { return 4.0 * Math.PI * Area / (Perimeter * Perimeter); } }
        public double Aspect
        {
            get
            {
                int width = MaxX - MinX + 1;
                int height = MaxY - MinY + 1;
                return (double)Math.Min(width, height) / Math.Max(width, height);
            }
        }
    }

    private static void VisitNeighbor(int x, int y, int dx, int dy, int width, int height,
        int threshold, int[] gray, bool[] visited, int[] queue, ref int write, Component component)
    {
        int nx = x + dx;
        int ny = y + dy;
        if (nx < 0 || nx >= width || ny < 0 || ny >= height || gray[ny * width + nx] < threshold)
        {
            component.Perimeter++;
            return;
        }
        int adjacent = ny * width + nx;
        if (!visited[adjacent])
        {
            visited[adjacent] = true;
            queue[write++] = adjacent;
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            string imagePath = null;
            string outputPath = null;
            string annotatedPath = null;
            int threshold = 190;
            for (int i = 0; i < args.Length; i++)
            {
                if (i + 1 >= args.Length) throw new ArgumentException("Every option requires a value.");
                string value = args[++i];
                if (args[i - 1] == "--image") imagePath = value;
                else if (args[i - 1] == "--out-csv") outputPath = value;
                else if (args[i - 1] == "--annotated") annotatedPath = value;
                else if (args[i - 1] == "--threshold") threshold = Int32.Parse(value, CultureInfo.InvariantCulture);
                else throw new ArgumentException("Unknown option: " + args[i - 1]);
            }
            if (String.IsNullOrWhiteSpace(imagePath) || String.IsNullOrWhiteSpace(outputPath) || threshold < 1 || threshold > 254)
                throw new ArgumentException("Usage: DetectMarkers.exe --image FRAME.jpg --out-csv CENTERS.csv [--annotated PREVIEW.png] [--threshold 1-254]");

            using (Image input = Image.FromFile(imagePath))
            using (Bitmap bitmap = new Bitmap(input.Width, input.Height, PixelFormat.Format24bppRgb))
            using (Graphics graphics = Graphics.FromImage(bitmap))
            {
                graphics.DrawImage(input, 0, 0, input.Width, input.Height);
                Rectangle bounds = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
                BitmapData data = bitmap.LockBits(bounds, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
                byte[] pixels = new byte[Math.Abs(data.Stride) * bitmap.Height];
                Marshal.Copy(data.Scan0, pixels, 0, pixels.Length);
                bitmap.UnlockBits(data);

                int width = bitmap.Width;
                int height = bitmap.Height;
                int[] gray = new int[width * height];
                for (int y = 0; y < height; y++)
                {
                    int row = data.Stride >= 0 ? y * data.Stride : (height - 1 - y) * -data.Stride;
                    int target = y * width;
                    for (int x = 0; x < width; x++)
                    {
                        int p = row + x * 3;
                        int blue = pixels[p];
                        int green = pixels[p + 1];
                        int red = pixels[p + 2];
                        gray[target + x] = (77 * red + 150 * green + 29 * blue) >> 8;
                    }
                }

                bool[] visited = new bool[gray.Length];
                int[] queue = new int[gray.Length];
                List<Component> accepted = new List<Component>();
                using (StreamWriter csv = new StreamWriter(outputPath, false))
                {
                    csv.WriteLine("id,cx_px,cy_px,area_px,bbox_x,bbox_y,bbox_width,bbox_height,aspect,circularity,mean_gray,max_gray");
                    int id = 0;
                    for (int seed = 0; seed < gray.Length; seed++)
                    {
                        if (visited[seed] || gray[seed] < threshold) continue;
                        Component component = new Component();
                        int read = 0;
                        int write = 0;
                        visited[seed] = true;
                        queue[write++] = seed;
                        while (read < write)
                        {
                            int index = queue[read++];
                            int x = index % width;
                            int y = index / width;
                            int value = gray[index];
                            component.Area++;
                            component.MinX = Math.Min(component.MinX, x);
                            component.MinY = Math.Min(component.MinY, y);
                            component.MaxX = Math.Max(component.MaxX, x);
                            component.MaxY = Math.Max(component.MaxY, y);
                            component.MaxGray = Math.Max(component.MaxGray, value);
                            component.SumGray += value;
                            double weight = value - threshold + 1;
                            component.WeightedX += x * weight;
                            component.WeightedY += y * weight;
                            component.Weight += weight;

                            VisitNeighbor(x, y, -1, 0, width, height, threshold, gray, visited, queue, ref write, component);
                            VisitNeighbor(x, y, 1, 0, width, height, threshold, gray, visited, queue, ref write, component);
                            VisitNeighbor(x, y, 0, -1, width, height, threshold, gray, visited, queue, ref write, component);
                            VisitNeighbor(x, y, 0, 1, width, height, threshold, gray, visited, queue, ref write, component);
                        }

                        int boxWidth = component.MaxX - component.MinX + 1;
                        int boxHeight = component.MaxY - component.MinY + 1;
                        if (component.Area < 20 || component.Area > 4000 ||
                            boxWidth < 5 || boxHeight < 5 || Math.Max(boxWidth, boxHeight) > 100 ||
                            component.Aspect < 0.35 || component.Circularity < 0.30 || component.Circularity > 1.35)
                            continue;

                        id++;
                        accepted.Add(component);
                        csv.WriteLine(String.Join(",", new string[] {
                            id.ToString(CultureInfo.InvariantCulture),
                            component.CenterX.ToString("F3", CultureInfo.InvariantCulture),
                            component.CenterY.ToString("F3", CultureInfo.InvariantCulture),
                            component.Area.ToString(CultureInfo.InvariantCulture),
                            component.MinX.ToString(CultureInfo.InvariantCulture),
                            component.MinY.ToString(CultureInfo.InvariantCulture),
                            boxWidth.ToString(CultureInfo.InvariantCulture),
                            boxHeight.ToString(CultureInfo.InvariantCulture),
                            component.Aspect.ToString("F3", CultureInfo.InvariantCulture),
                            component.Circularity.ToString("F3", CultureInfo.InvariantCulture),
                            ((double)component.SumGray / component.Area).ToString("F1", CultureInfo.InvariantCulture),
                            component.MaxGray.ToString(CultureInfo.InvariantCulture)
                        }));
                    }
                    Console.WriteLine("Detected " + id + " reflective-blob candidates at grayscale threshold " + threshold + ".");
                }

                if (!String.IsNullOrWhiteSpace(annotatedPath))
                {
                    using (Bitmap annotated = new Bitmap(input))
                    using (Graphics overlay = Graphics.FromImage(annotated))
                    using (Pen pen = new Pen(Color.Red, 2.0f))
                    using (Brush centerBrush = new SolidBrush(Color.Lime))
                    using (Font font = new Font(FontFamily.GenericSansSerif, 10.0f, FontStyle.Bold))
                    using (Brush labelBrush = new SolidBrush(Color.Yellow))
                    {
                        for (int i = 0; i < accepted.Count; i++)
                        {
                            Component component = accepted[i];
                            overlay.DrawRectangle(pen, component.MinX, component.MinY,
                                component.MaxX - component.MinX + 1, component.MaxY - component.MinY + 1);
                            overlay.FillEllipse(centerBrush, (float)component.CenterX - 2.0f,
                                (float)component.CenterY - 2.0f, 4.0f, 4.0f);
                            overlay.DrawString((i + 1).ToString(CultureInfo.InvariantCulture), font,
                                labelBrush, component.MinX, Math.Max(0, component.MinY - 16));
                        }
                        annotated.Save(annotatedPath, ImageFormat.Png);
                    }
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Marker detection failed: " + error.Message);
            return 1;
        }
    }
}
