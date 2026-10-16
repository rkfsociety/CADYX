using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

internal static class Program
{
    private static readonly Guid ServiceUuid = Guid.Parse("0000FFE0-0000-1000-8000-00805F9B34FB");
    private static readonly Guid CharacteristicUuid = Guid.Parse("0000FFE1-0000-1000-8000-00805F9B34FB");
    private const string DeviceName = "REVO_DUAL_AXIS_TABLE";
    private const double MaxDegreesPerCommand = 30.0;
    private const double MinRotationPeriodSeconds = 18.0;
    private const double MaxRotationPeriodSeconds = 90.0;

    private sealed class Connection : IDisposable
    {
        public BluetoothLEDevice Device;
        public GattDeviceService Service;
        public GattCharacteristic Characteristic;
        public ConcurrentQueue<string> Responses = new ConcurrentQueue<string>();
        public TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> Handler;
        public bool NotificationsEnabled;

        public void Dispose()
        {
            if (Characteristic != null && Handler != null)
            {
                Characteristic.ValueChanged -= Handler;
            }
            if (NotificationsEnabled && Characteristic != null)
            {
                Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
                    GattClientCharacteristicConfigurationDescriptorValue.None).AsTask().GetAwaiter().GetResult();
            }
            if (Service != null) Service.Dispose();
            if (Device != null) Device.Dispose();
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0 || args[0] == "--help" || args[0] == "-h")
            {
                Usage();
                return args.Length == 0 ? 2 : 0;
            }

            string operation = args[0].ToLowerInvariant();
            double degrees = 0;
            double rotationPeriod = 0;
            int monitorSeconds = 0;
            string monitorPath = null;
            int rotateAfterSeconds = -1;
            if (operation == "rotate")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand)
                {
                    throw new ArgumentException("rotate requires a number from -30 to 30 degrees, excluding zero.");
                }
            }
            else if (operation == "speed")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out rotationPeriod) || !IsFinite(rotationPeriod) ||
                    rotationPeriod < MinRotationPeriodSeconds || rotationPeriod > MaxRotationPeriodSeconds)
                {
                    throw new ArgumentException("speed requires seconds per rotation from 18 to 90.");
                }
            }
            else if (operation == "monitor")
            {
                if ((args.Length != 3 && args.Length != 5) || !Int32.TryParse(args[1], out monitorSeconds) || monitorSeconds < 1 || monitorSeconds > 300)
                    throw new ArgumentException("monitor requires duration (1–300 s), CSV path, and optional rotate-delay plus degrees.");
                monitorPath = args[2];
                if (args.Length == 5 && (!Int32.TryParse(args[3], out rotateAfterSeconds) || rotateAfterSeconds < 0 || rotateAfterSeconds >= monitorSeconds ||
                    !double.TryParse(args[4], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand))
                    throw new ArgumentException("monitor rotation requires delay < duration and degrees from -30 to 30.");
            }
            else if ((operation == "query" || operation == "zero" || operation == "stop") && args.Length != 1)
            {
                throw new ArgumentException("query, zero and stop do not accept extra arguments.");
            }
            else if (operation != "query" && operation != "zero" && operation != "stop" && operation != "speed" && operation != "monitor")
            {
                throw new ArgumentException("Operation must be query, zero, speed <seconds-per-rotation>, rotate <degrees>, or stop.");
            }

            return RunAsync(operation, degrees, rotationPeriod, monitorSeconds, monitorPath, rotateAfterSeconds).GetAwaiter().GetResult();
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Turntable command failed: " + error.Message);
            return 1;
        }
    }

    private static void Usage()
    {
        Console.WriteLine("TurntableController.exe query");
        Console.WriteLine("TurntableController.exe zero");
        Console.WriteLine("TurntableController.exe speed <seconds-per-rotation>   (18–90 seconds)");
        Console.WriteLine("TurntableController.exe rotate <degrees>   (one bounded relative move, max 30 degrees)");
        Console.WriteLine("TurntableController.exe stop");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv>");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv> <rotate-delay-seconds> <degrees>");
        Console.WriteLine("The app does not pair the turntable. It discovers the BLE device by its advertised FFE1 service,\n" +
                          "then confirms the GATT device name before sending any command.");
    }

    private static async Task<int> RunAsync(string operation, double degrees, double rotationPeriod,
                                            int monitorSeconds, string monitorPath, int rotateAfterSeconds)
    {
        using (Connection connection = await ConnectAsync())
        {
            await EnableNotificationsAsync(connection);
            if (operation == "query")
            {
                foreach (string command in new[] { "+QT,CHANGEANGLE;", "+QR,TILTANGLE;", "+QT,TURNSPEED;", "+QR,TILTSPEED;" })
                {
                    Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                }
                return 0;
            }

            if (operation == "monitor")
            {
                string fullPath = System.IO.Path.GetFullPath(monitorPath);
                using (var writer = new System.IO.StreamWriter(fullPath, false, new UTF8Encoding(false)))
                {
                    writer.WriteLine("utc,angle_degrees,raw_response");
                    var monitorDeadline = DateTime.UtcNow.AddSeconds(monitorSeconds);
                    var monitorTimer = Stopwatch.StartNew();
                    int sample = 0;
                    bool moveSent = false;
                    while (DateTime.UtcNow < monitorDeadline)
                    {
                        if (!moveSent && rotateAfterSeconds >= 0 && monitorTimer.Elapsed.TotalSeconds >= rotateAfterSeconds)
                        {
                            string monitorMove = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                            Console.WriteLine("issuing " + monitorMove);
                            string reply = await SendAndReadAsync(connection, monitorMove);
                            writer.WriteLine("# " + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " command " + monitorMove + " => " + reply);
                            writer.Flush();
                            moveSent = true;
                        }
                        string response = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                        double angle;
                        DateTime observedAt = DateTime.UtcNow;
                        if (TryAngle(response, out angle))
                        {
                            writer.WriteLine(observedAt.ToString("o", CultureInfo.InvariantCulture) + "," +
                                angle.ToString("F2", CultureInfo.InvariantCulture) + ",\"" + response.Replace("\"", "\"\"") + "\"");
                            writer.Flush();
                            if (sample++ % 5 == 0) Console.WriteLine(observedAt.ToString("o") + " angle=" + angle.ToString("F1", CultureInfo.InvariantCulture));
                        }
                        await Task.Delay(50);
                    }
                }
                return 0;
            }

            if (operation == "stop")
            {
                Console.WriteLine("rotation stop: " + await SendAndReadAsync(connection, "+CT,STOP;"));
                Console.WriteLine("tilt stop: " + await SendAndReadAsync(connection, "+CR,STOP;"));
                return 0;
            }

            if (operation == "speed")
            {
                string command = "+CT,TURNSPEED=" + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + ";";
                Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                Console.WriteLine("Requested rotation period: " + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + " seconds per revolution.");
                return 0;
            }

            if (operation == "zero")
            {
                string zeroBaselineText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                double zeroBaseline;
                if (!TryAngle(zeroBaselineText, out zeroBaseline))
                {
                    throw new InvalidOperationException("Could not read the current angle; zero command was not sent.");
                }
                Console.WriteLine("angle before zero: " + zeroBaseline.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                string reply = await SendAndReadAsync(connection, "+CT,TOZERO;");
                Console.WriteLine("+CT,TOZERO; => " + reply);
                for (int attempt = 0; attempt < 30; ++attempt)
                {
                    await Task.Delay(1000);
                    string currentText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                    double current;
                    if (TryAngle(currentText, out current))
                    {
                        double normalized = SignedAngleDelta(0, current);
                        if (Math.Abs(normalized) <= 0.5)
                        {
                            Console.WriteLine("angle after zero: " + normalized.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                            return 0;
                        }
                    }
                }
                Console.Error.WriteLine("Table did not report zero within 30 checks; no automatic retry was sent.");
                return 1;
            }

            string beforeText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
            double before;
            if (!TryAngle(beforeText, out before))
            {
                throw new InvalidOperationException("Could not read the current angle; no movement was sent.");
            }
            Console.WriteLine("angle before: " + before.ToString("F1", CultureInfo.InvariantCulture) + " deg");

            string move = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
            var timer = Stopwatch.StartNew();
            string moveReply = await SendAndReadAsync(connection, move);
            Console.WriteLine(move + " => " + moveReply);
            double after = Double.NaN;
            double measured = Double.NaN;
            bool reachedTarget = false;
            DateTime deadline = DateTime.UtcNow.AddSeconds(10.0 + Math.Abs(degrees) * 90.0 / 360.0);
            while (DateTime.UtcNow < deadline)
            {
                await Task.Delay(200);
                string afterText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                if (!TryAngle(afterText, out after)) continue;
                measured = SignedAngleDelta(before, after);
                if (Math.Abs(measured - degrees) <= 1.0)
                {
                    reachedTarget = true;
                    timer.Stop();
                    break;
                }
            }
            if (Double.IsNaN(after))
            {
                Console.WriteLine("angle after: unavailable; no further motor command was sent");
                return 1;
            }
            Console.WriteLine("angle after: " + after.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            Console.WriteLine("measured change: " + measured.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            if (reachedTarget)
            {
                Console.WriteLine("movement completion time: " + timer.Elapsed.TotalSeconds.ToString("F2", CultureInfo.InvariantCulture) + " s");
            }
            if (!reachedTarget)
            {
                Console.Error.WriteLine("Rotation did not reach the target before timeout; do not retry automatically.");
                return 1;
            }
            return 0;
        }
    }

    private static async Task<Connection> ConnectAsync()
    {
        Console.WriteLine("Searching nearby BLE devices for the Revopoint turntable...");
        var addresses = new ConcurrentDictionary<ulong, byte>();
        var watcher = new BluetoothLEAdvertisementWatcher { ScanningMode = BluetoothLEScanningMode.Active };
        TypedEventHandler<BluetoothLEAdvertisementWatcher, BluetoothLEAdvertisementReceivedEventArgs> handler = (sender, eventArgs) =>
        {
            if (eventArgs.Advertisement.ServiceUuids.Contains(CharacteristicUuid))
            {
                addresses[eventArgs.BluetoothAddress] = 0;
            }
        };
        watcher.Received += handler;
        try
        {
            watcher.Start();
            await Task.Delay(TimeSpan.FromSeconds(8));
        }
        finally
        {
            try { watcher.Stop(); } catch { }
            watcher.Received -= handler;
        }

        foreach (ulong address in addresses.Keys)
        {
            BluetoothLEDevice device = null;
            try
            {
                device = await BluetoothLEDevice.FromBluetoothAddressAsync(address);
                if (device == null || !String.Equals(device.Name, DeviceName, StringComparison.Ordinal))
                {
                    if (device != null) device.Dispose();
                    continue;
                }

                var serviceResult = await device.GetGattServicesForUuidAsync(ServiceUuid, BluetoothCacheMode.Uncached);
                if (serviceResult.Status != GattCommunicationStatus.Success || serviceResult.Services.Count != 1)
                {
                    foreach (var unmatchedService in serviceResult.Services) unmatchedService.Dispose();
                    device.Dispose();
                    continue;
                }

                GattDeviceService service = serviceResult.Services[0];
                var characteristicResult = await service.GetCharacteristicsForUuidAsync(CharacteristicUuid, BluetoothCacheMode.Uncached);
                if (characteristicResult.Status != GattCommunicationStatus.Success || characteristicResult.Characteristics.Count != 1)
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }

                GattCharacteristic characteristic = characteristicResult.Characteristics[0];
                var properties = characteristic.CharacteristicProperties;
                if ((properties & GattCharacteristicProperties.Notify) == 0 ||
                    ((properties & GattCharacteristicProperties.WriteWithoutResponse) == 0 &&
                     (properties & GattCharacteristicProperties.Write) == 0))
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }
                Console.WriteLine("Connected to " + DeviceName + " over BLE (no pairing performed).");
                return new Connection { Device = device, Service = service, Characteristic = characteristic };
            }
            catch
            {
                if (device != null) device.Dispose();
            }
        }
        throw new InvalidOperationException("No nearby BLE device matched REVO_DUAL_AXIS_TABLE with the expected GATT service.");
    }

    private static async Task EnableNotificationsAsync(Connection connection)
    {
        connection.Handler = (sender, eventArgs) => connection.Responses.Enqueue(ReadAscii(eventArgs.CharacteristicValue));
        connection.Characteristic.ValueChanged += connection.Handler;
        var status = await connection.Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
            GattClientCharacteristicConfigurationDescriptorValue.Notify);
        if (status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("Could not enable turntable status notifications: " + status);
        }
        connection.NotificationsEnabled = true;
    }

    private static async Task<string> SendAndReadAsync(Connection connection, string command)
    {
        string ignored;
        while (connection.Responses.TryDequeue(out ignored)) { }

        var writer = new DataWriter();
        writer.WriteBytes(Encoding.ASCII.GetBytes(command + "\r\n"));
        GattWriteOption option =
            (connection.Characteristic.CharacteristicProperties & GattCharacteristicProperties.WriteWithoutResponse) != 0
                ? GattWriteOption.WriteWithoutResponse
                : GattWriteOption.WriteWithResponse;
        var writeStatus = await connection.Characteristic.WriteValueAsync(writer.DetachBuffer(), option);
        if (writeStatus != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("BLE write failed for " + command + ": " + writeStatus);
        }
        await Task.Delay(200);

        string response;
        string last = null;
        while (connection.Responses.TryDequeue(out response)) last = response;
        if (last != null) return last;

        var read = await connection.Characteristic.ReadValueAsync(BluetoothCacheMode.Uncached);
        if (read.Status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("No BLE notification and readback failed for " + command + ".");
        }
        return ReadAscii(read.Value);
    }

    private static string ReadAscii(IBuffer buffer)
    {
        if (buffer == null || buffer.Length == 0) return "<empty>";
        var reader = DataReader.FromBuffer(buffer);
        var bytes = new byte[reader.UnconsumedBufferLength];
        reader.ReadBytes(bytes);
        return Encoding.ASCII.GetString(bytes).Replace("\r", "").Replace("\n", "");
    }

    private static bool TryAngle(string response, out double angle)
    {
        Match match = Regex.Match(response ?? String.Empty, @"\+DATA=\s*(-?\d+(?:\.\d+)?)");
        return double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out angle);
    }

    private static double SignedAngleDelta(double before, double after)
    {
        double delta = after - before;
        if (delta > 180.0) delta -= 360.0;
        if (delta < -180.0) delta += 360.0;
        return delta;
    }

    private static bool IsFinite(double value)
    {
        return !Double.IsNaN(value) && !Double.IsInfinity(value);
    }
}
