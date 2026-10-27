/**
 * TikTok Quality – Client-side MP4 Container Transformation Engine
 * Original algorithm by Bastien Gimbert (MIT License)
 * JavaScript port by H4B Official
 * 
 * Bypasses TikTok 1080p60 re-compression by rewriting the MP4 container:
 * - Strips non-IDR/IDR SEI metadata in the first frame.
 * - Injects "ghost frames" table entries pointing to an 8-byte filler block.
 * - Patches avcC, bitrate (btrt), and frame tables (stts, stsz, stsc, stco).
 * - Converts layout to fast-start (moov header before mdat video data).
 * - Appends iTunes-style metadata comment tag.
 */

(function (global) {
  'use strict';

  const PADDING_NAL = new Uint8Array([0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00]);
  const PADDING_SIZE = 8;
  const KEEP_NALU_TYPES = new Set([1, 5, 7, 8]); // 1: non-IDR slice, 5: IDR slice, 7: SPS, 8: PPS
  const LANG_UND = 0x55c4; // ISO 639-2/T packed 'und'

  function readU32(bytes, offset) {
    return ((bytes[offset] << 24) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]) >>> 0;
  }

  function readU16(bytes, offset) {
    return (bytes[offset] << 8) | bytes[offset + 1];
  }

  function writeU32(bytes, offset, value) {
    bytes[offset] = (value >>> 24) & 0xff;
    bytes[offset + 1] = (value >>> 16) & 0xff;
    bytes[offset + 2] = (value >>> 8) & 0xff;
    bytes[offset + 3] = value & 0xff;
  }

  function writeU16(bytes, offset, value) {
    bytes[offset] = (value >>> 8) & 0xff;
    bytes[offset + 1] = value & 0xff;
  }

  function strToBytes(str) {
    const bytes = new Uint8Array(str.length);
    for (let i = 0; i < str.length; i++) {
      bytes[i] = str.charCodeAt(i) & 0xff;
    }
    return bytes;
  }

  function utf8ToBytes(str) {
    return new TextEncoder().encode(str);
  }

  function bytesToStr(bytes, start, length) {
    let str = '';
    for (let i = start; i < start + length; i++) {
      str += String.fromCharCode(bytes[i]);
    }
    return str;
  }

  function concatBytes(...arrays) {
    let totalLen = 0;
    for (let i = 0; i < arrays.length; i++) {
      totalLen += arrays[i].length;
    }
    const result = new Uint8Array(totalLen);
    let offset = 0;
    for (let i = 0; i < arrays.length; i++) {
      result.set(arrays[i], offset);
      offset += arrays[i].length;
    }
    return result;
  }

  function findBox(data, boxType, offset = 0, end = null) {
    if (end === null) end = data.length;
    let pos = offset;
    while (pos + 8 <= end) {
      const size = readU32(data, pos);
      const btype = bytesToStr(data, pos + 4, 4);
      if (size < 8) break;
      if (btype === boxType) {
        return [pos, size];
      }
      pos += size;
    }
    return [null, null];
  }

  function findBoxPath(data, path, offset = 0, end = null) {
    if (end === null) end = data.length;
    let currentOffset = offset;
    let currentEnd = end;

    for (let i = 0; i < path.length; i++) {
      const boxType = path[i];
      let pos = currentOffset;
      let found = false;
      while (pos + 8 <= currentEnd) {
        const size = readU32(data, pos);
        const btype = bytesToStr(data, pos + 4, 4);
        if (size < 8) break;
        if (btype === boxType) {
          if (i === path.length - 1) {
            return [pos, size];
          }
          currentOffset = pos + 8;
          currentEnd = pos + size;
          found = true;
          break;
        }
        pos += size;
      }
      if (!found) return [null, null];
    }
    return [null, null];
  }

  function findTrackByHandler(data, moovOffset, moovSize, handlerBytes) {
    let pos = moovOffset + 8;
    const end = moovOffset + moovSize;
    while (pos + 8 <= end) {
      const size = readU32(data, pos);
      const btype = bytesToStr(data, pos + 4, 4);
      if (size < 8) break;
      if (btype === 'trak') {
        const [hdlrPos] = findBoxPath(data, ['mdia', 'hdlr'], pos + 8, pos + size);
        if (hdlrPos !== null) {
          let match = true;
          for (let j = 0; j < handlerBytes.length; j++) {
            if (data[hdlrPos + 16 + j] !== handlerBytes[j]) {
              match = false;
              break;
            }
          }
          if (match) return [pos, size];
        }
      }
      pos += size;
    }
    return [null, null];
  }

  // Audio track tabhi use hota hai jab uske saare zaroori boxes maujood hon.
  function audioTrackUsable(data, atPos, atSize) {
    const s = atPos + 8, e = atPos + atSize;
    const [stbl, stblSize] = findBoxPath(data, ['mdia', 'minf', 'stbl'], s, e);
    if (stbl === null) return false;
    const bs = stbl + 8, be = stbl + stblSize;
    const hasOffsets = findBox(data, 'stco', bs, be)[0] !== null || findBox(data, 'co64', bs, be)[0] !== null;
    return hasOffsets &&
      ['stsz', 'stsc', 'stts', 'stsd'].every((t) => findBox(data, t, bs, be)[0] !== null) &&
      findBox(data, 'tkhd', s, e)[0] !== null &&
      findBoxPath(data, ['mdia', 'mdhd'], s, e)[0] !== null &&
      findBoxPath(data, ['mdia', 'minf', 'smhd'], s, e)[0] !== null &&
      findBoxPath(data, ['mdia', 'minf', 'dinf'], s, e)[0] !== null;
  }

  function parseStts(data, offset) {
    const entryCount = readU32(data, offset + 12);
    const entries = [];
    for (let i = 0; i < entryCount; i++) {
      entries.push([
        readU32(data, offset + 16 + i * 8),
        readU32(data, offset + 20 + i * 8)
      ]);
    }
    return entries;
  }

  function parseStsz(data, offset) {
    const uniform = readU32(data, offset + 12);
    const count = readU32(data, offset + 16);
    if (uniform !== 0) {
      return new Array(count).fill(uniform);
    }
    const sizes = [];
    for (let i = 0; i < count; i++) {
      sizes.push(readU32(data, offset + 20 + i * 4));
    }
    return sizes;
  }

  function parseStsc(data, offset) {
    const entryCount = readU32(data, offset + 12);
    const entries = [];
    for (let i = 0; i < entryCount; i++) {
      entries.push([
        readU32(data, offset + 16 + i * 12),
        readU32(data, offset + 20 + i * 12),
        readU32(data, offset + 24 + i * 12)
      ]);
    }
    return entries;
  }

  function parseStco(data, offset) {
    const entryCount = readU32(data, offset + 12);
    const offsets = [];
    for (let i = 0; i < entryCount; i++) {
      offsets.push(readU32(data, offset + 16 + i * 4));
    }
    return offsets;
  }

  function parseCo64(data, offset) {
    const entryCount = readU32(data, offset + 12);
    const offsets = [];
    for (let i = 0; i < entryCount; i++) {
      // Lower 32-bit word represents chunk offset for files < 4GB
      offsets.push(readU32(data, offset + 20 + i * 8));
    }
    return offsets;
  }

  function parseNaluList(sampleData) {
    const nalus = [];
    let pos = 0;
    while (pos + 4 <= sampleData.length) {
      const naluLen = readU32(sampleData, pos);
      if (pos + 4 + naluLen > sampleData.length) break;
      const naluType = sampleData[pos + 4] & 0x1f;
      nalus.push({
        type: naluType,
        length: naluLen,
        data: sampleData.subarray(pos, pos + 4 + naluLen)
      });
      pos += 4 + naluLen;
    }
    return nalus;
  }

  function buildBox(boxType, content) {
    const res = new Uint8Array(8 + content.length);
    writeU32(res, 0, 8 + content.length);
    for (let i = 0; i < 4; i++) {
      res[4 + i] = boxType.charCodeAt(i) & 0xff;
    }
    res.set(content, 8);
    return res;
  }

  function buildFtyp(brand = 'isom', minorVersion = 512, compatible = 'isomiso2avc1mp41') {
    const bBrand = strToBytes(brand);
    const bVer = new Uint8Array(4);
    writeU32(bVer, 0, minorVersion);
    const bComp = strToBytes(compatible);
    return buildBox('ftyp', concatBytes(bBrand, bVer, bComp));
  }

  function buildFree() {
    const res = new Uint8Array(8);
    writeU32(res, 0, 8);
    res.set(strToBytes('free'), 4);
    return res;
  }

  function buildMvhd(originalContent, durationMs) {
    const content = new Uint8Array(originalContent);
    writeU32(content, 16, durationMs);
    return buildBox('mvhd', content);
  }

  function buildMdhd(originalContent, duration = null, language = null) {
    const content = new Uint8Array(originalContent);
    if (duration !== null) {
      writeU32(content, 16, duration);
    }
    if (language !== null) {
      writeU16(content, 20, language);
    }
    return buildBox('mdhd', content);
  }

  function buildTkhd(originalData, durationMs = null) {
    const result = new Uint8Array(originalData);
    if (durationMs !== null) {
      writeU32(result, 28, durationMs);
    }
    return result;
  }

  function buildHdlr(handlerTypeBytes, name) {
    const hdlrContent = new Uint8Array(24 + name.length + 1);
    hdlrContent.set(handlerTypeBytes, 8);
    const nameBytes = utf8ToBytes(name);
    hdlrContent.set(nameBytes, 24);
    hdlrContent[24 + nameBytes.length] = 0;
    return buildBox('hdlr', hdlrContent);
  }

  function buildStts(entries) {
    const body = new Uint8Array(8 + entries.length * 8);
    writeU32(body, 0, 0);
    writeU32(body, 4, entries.length);
    for (let i = 0; i < entries.length; i++) {
      writeU32(body, 8 + i * 8, entries[i][0]);
      writeU32(body, 12 + i * 8, entries[i][1]);
    }
    return buildBox('stts', body);
  }

  function buildStsc(entries) {
    const body = new Uint8Array(8 + entries.length * 12);
    writeU32(body, 0, 0);
    writeU32(body, 4, entries.length);
    for (let i = 0; i < entries.length; i++) {
      writeU32(body, 8 + i * 12, entries[i][0]);
      writeU32(body, 12 + i * 12, entries[i][1]);
      writeU32(body, 16 + i * 12, entries[i][2]);
    }
    return buildBox('stsc', body);
  }

  function buildStsz(sampleSizes) {
    const body = new Uint8Array(12 + sampleSizes.length * 4);
    writeU32(body, 0, 0);
    writeU32(body, 4, 0);
    writeU32(body, 8, sampleSizes.length);
    for (let i = 0; i < sampleSizes.length; i++) {
      writeU32(body, 12 + i * 4, sampleSizes[i]);
    }
    return buildBox('stsz', body);
  }

  function buildStco(offsets) {
    const body = new Uint8Array(8 + offsets.length * 4);
    writeU32(body, 0, 0);
    writeU32(body, 4, offsets.length);
    for (let i = 0; i < offsets.length; i++) {
      writeU32(body, 8 + i * 4, offsets[i]);
    }
    return buildBox('stco', body);
  }

  function buildAvcc(originalContent, addHighExt = true) {
    let content = originalContent;
    const len = originalContent.length;
    const alreadyHasExt = len >= 4 &&
      originalContent[len - 4] === 0xfd &&
      originalContent[len - 3] === 0xf8 &&
      originalContent[len - 2] === 0xf8 &&
      originalContent[len - 1] === 0x00;

    if (addHighExt && !alreadyHasExt) {
      content = concatBytes(originalContent, new Uint8Array([0xfd, 0xf8, 0xf8, 0x00]));
    }
    return buildBox('avcC', content);
  }

  function buildBtrt(bufferSizeDb, maxBitrate, avgBitrate) {
    const content = new Uint8Array(12);
    writeU32(content, 0, bufferSizeDb);
    writeU32(content, 4, maxBitrate);
    writeU32(content, 8, avgBitrate);
    return buildBox('btrt', content);
  }

  function buildStsdVideo(fixedFields, avcc, colr, pasp, btrt) {
    const avc1Content = concatBytes(fixedFields, avcc, colr, pasp, btrt);
    const avc1Header = new Uint8Array(8);
    writeU32(avc1Header, 0, 8 + avc1Content.length);
    avc1Header.set(strToBytes('avc1'), 4);
    const avc1 = concatBytes(avc1Header, avc1Content);

    const stsdHeader = new Uint8Array(8);
    writeU32(stsdHeader, 0, 0);
    writeU32(stsdHeader, 4, 1);
    return buildBox('stsd', concatBytes(stsdHeader, avc1));
  }

  function buildUdtaComment(comment) {
    const metaHdlrContent = new Uint8Array(25);
    metaHdlrContent.set(strToBytes('mdirappl'), 8);
    const metaHdlr = buildBox('hdlr', metaHdlrContent);

    const commentUtf8 = utf8ToBytes(comment);
    const dataBoxContent = new Uint8Array(8 + commentUtf8.length);
    writeU32(dataBoxContent, 0, 1);
    writeU32(dataBoxContent, 4, 0);
    dataBoxContent.set(commentUtf8, 8);
    const dataBox = buildBox('data', dataBoxContent);

    const cmtHeader = new Uint8Array(8);
    writeU32(cmtHeader, 0, 8 + dataBox.length);
    cmtHeader.set(new Uint8Array([0xa9, 0x63, 0x6d, 0x74]), 4);
    const cmt = concatBytes(cmtHeader, dataBox);

    const ilst = buildBox('ilst', cmt);
    const metaContent = concatBytes(new Uint8Array(4), metaHdlr, ilst);
    const meta = buildBox('meta', metaContent);
    return buildBox('udta', meta);
  }

  function findSubarray(haystack, needle, start = 0) {
    for (let i = start; i <= haystack.length - needle.length; i++) {
      let match = true;
      for (let j = 0; j < needle.length; j++) {
        if (haystack[i + j] !== needle[j]) {
          match = false;
          break;
        }
      }
      if (match) return i;
    }
    return -1;
  }

  /**
   * Transforms an MP4 video container according to TikTok quality optimization rules.
   * @param {Uint8Array|ArrayBuffer} inputData - MP4 file buffer.
   * @param {Object} [options]
   * @param {number} [options.multiplier=10] - Integer 1..50. 1 = no ghost frames (exact frame count). >1 = ghost-frame inflation (declared frames x multiplier).
   * @param {string} [options.comment='TK8vY5VqBA6hUlo1yuGvNA'] - Metadata comment tag (max 64 chars). Pass '' to write no tag.
   * @param {function} [options.onProgress] - Optional progress callback: (percent: number, step: string) => void.
   * @returns {{ output: Uint8Array, stats: Object }}
   */
  function transformMp4(inputData, options = {}) {
    const data = inputData instanceof Uint8Array ? inputData : new Uint8Array(inputData);
    const rawMultiplier = options.multiplier !== undefined ? Number(options.multiplier) : 10;
    const multiplier = Number.isFinite(rawMultiplier) ? Math.max(1, Math.min(50, Math.floor(rawMultiplier))) : 1;
    const comment = String(options.comment !== undefined && options.comment !== null ? options.comment : 'TK8vY5VqBA6hUlo1yuGvNA').slice(0, 64);
    const onProgress = options.onProgress || (() => {});
    const fileSize = data.length;
    const assertV0 = (pos, name) => {
      if (pos === null || data[pos + 8] !== 0) {
        throw new Error('Unsupported MP4: ' + name + ' box is missing or uses 64-bit (version 1) fields.');
      }
    };

    onProgress(10, 'Scanning MP4 boxes...');
    const [moovPos, moovSize] = findBox(data, 'moov');
    const [mdatPos, mdatSize] = findBox(data, 'mdat');
    if (moovPos === null || mdatPos === null) {
      throw new Error('Unsupported file: missing moov or mdat box. File must be a standard MP4.');
    }

    // NOTE: neeche jin variables ke naam '...Ms' hain wo asal mein movie-timescale units mein hain.
    const [mvhdPos0] = findBox(data, 'mvhd', moovPos + 8, moovPos + moovSize);
    assertV0(mvhdPos0, 'mvhd');
    const mvTimescale = readU32(data, mvhdPos0 + 20) || 1000; // iPhone = 600, ffmpeg/Android = 1000

    const mdatDataStart = mdatPos + 8;

    onProgress(20, 'Analyzing video track...');
    const videBytes = strToBytes('vide');
    const [vtPos, vtSize] = findTrackByHandler(data, moovPos, moovSize, videBytes);
    if (vtPos === null) {
      throw new Error('No video track found. Video must have an H.264/AVC track.');
    }

    const vtStart = vtPos + 8;
    const vtEnd = vtPos + vtSize;
    assertV0(findBox(data, 'tkhd', vtStart, vtEnd)[0], 'video tkhd');
    const [stblPos, stblSize] = findBoxPath(data, ['mdia', 'minf', 'stbl'], vtStart, vtEnd);
    if (stblPos === null) {
      throw new Error('Invalid MP4 structure: missing video sample table (stbl).');
    }
    const sbStart = stblPos + 8;
    const sbEnd = stblPos + stblSize;

    const [stcoPos] = findBox(data, 'stco', sbStart, sbEnd);
    const [co64Pos] = stcoPos === null ? findBox(data, 'co64', sbStart, sbEnd) : [null];
    const [stszPos] = findBox(data, 'stsz', sbStart, sbEnd);
    const [stscPos] = findBox(data, 'stsc', sbStart, sbEnd);
    const [sttsPos] = findBox(data, 'stts', sbStart, sbEnd);
    const [stsdPos, stsdSize] = findBox(data, 'stsd', sbStart, sbEnd);
    const [stssPos, stssSize] = findBox(data, 'stss', sbStart, sbEnd);
    const [sdtpPos, sdtpSize] = findBox(data, 'sdtp', sbStart, sbEnd);
    const [cttsPos, cttsSize] = findBox(data, 'ctts', sbStart, sbEnd);

    if ((stcoPos === null && co64Pos === null) || !stszPos || !stscPos || !sttsPos || !stsdPos) {
      throw new Error('Invalid MP4: missing critical sample tables in video track.');
    }

    // Codec guard: sirf H.264/AVC (avcC) aur 4-byte NAL length support hai.
    {
      const stsdBody = data.subarray(stsdPos + 8, stsdPos + stsdSize);
      const avcCIdx = findSubarray(stsdBody, strToBytes('avcC'));
      if (avcCIdx < 4) {
        throw new Error('Unsupported video codec: only H.264/AVC (avcC) is supported. HEVC/H.265, AV1 and VP9 are not.');
      }
      if ((stsdBody[avcCIdx + 8] & 0x03) !== 3) {
        throw new Error('Unsupported H.264 stream: only 4-byte NAL length prefixes are supported.');
      }
    }

    const videoChunks = stcoPos !== null ? parseStco(data, stcoPos) : parseCo64(data, co64Pos);
    const videoSizes = parseStsz(data, stszPos);
    const videoStsc = parseStsc(data, stscPos);
    const videoStts = parseStts(data, sttsPos);

    if (!videoChunks.length || !videoSizes.length || !videoStts.length) {
      throw new Error('Invalid MP4: video stream contains 0 frames or empty sample tables.');
    }

    const origFrames = videoSizes.length;
    const timeDelta = videoStts[0][1];
    const padCount = multiplier > 1 ? origFrames * (multiplier - 1) : 0;
    const totalFrames = origFrames + padCount;

    const [vMdhdPos, vMdhdSize] = findBoxPath(data, ['mdia', 'mdhd'], vtStart, vtEnd);
    if (vMdhdPos === null) {
      throw new Error('Invalid MP4: missing video media header (mdhd).');
    }
    assertV0(vMdhdPos, 'video mdhd');
    const vTimescale = readU32(data, vMdhdPos + 20);
    const vDuration = readU32(data, vMdhdPos + 24);
    const vDurationSec = vTimescale > 0 ? (vDuration / vTimescale) : 0;

    onProgress(35, 'Cleaning SEI metadata from first frame...');
    const firstOff = videoChunks[0];
    const firstSize = videoSizes[0];
    const firstSample = data.subarray(firstOff, firstOff + firstSize);

    const nalus = parseNaluList(firstSample);
    const kept = nalus.filter(n => KEEP_NALU_TYPES.has(n.type)).map(n => n.data);
    const newFirst = kept.length > 0 ? concatBytes(...kept) : firstSample;
    const newFirstSize = newFirst.length;
    const seiRemoved = firstSize - newFirstSize;

    onProgress(45, 'Checking audio track...');
    const sounBytes = strToBytes('soun');
    const [atPos, atSize] = findTrackByHandler(data, moovPos, moovSize, sounBytes);
    let hasAudio = atPos !== null && audioTrackUsable(data, atPos, atSize);
    if (hasAudio) {
      assertV0(findBox(data, 'tkhd', atPos + 8, atPos + atSize)[0], 'audio tkhd');
      assertV0(findBoxPath(data, ['mdia', 'mdhd'], atPos + 8, atPos + atSize)[0], 'audio mdhd');
    }

    let audioChunks = [], audioSizes = [], audioStts = [];
    let aTimescale = 0, newAudioDur = 0, newAudioDurMs = 0;
    let newAStts = [], oldABr = 0, newABr = 0;
    let atStart = 0, atEnd = 0;
    let aStsdPos = null, aStsdSize = 0, aStscPos = null, aStscSize = 0;
    let aStszPos = null, aStszSize = 0, aMdhdPos = null, aMdhdSize = 0;
    let aSgpdPos = null, aSgpdSize = 0, aSbgpPos = null, aSbgpSize = 0;

    if (hasAudio) {
      atStart = atPos + 8;
      atEnd = atPos + atSize;
      const [aStblPos, aStblSize] = findBoxPath(data, ['mdia', 'minf', 'stbl'], atStart, atEnd);
      const aSbStart = aStblPos + 8;
      const aSbEnd = aStblPos + aStblSize;

      const [aStcoPos] = findBox(data, 'stco', aSbStart, aSbEnd);
      const [aCo64Pos] = aStcoPos === null ? findBox(data, 'co64', aSbStart, aSbEnd) : [null];
      [aStszPos, aStszSize] = findBox(data, 'stsz', aSbStart, aSbEnd);
      [aStscPos, aStscSize] = findBox(data, 'stsc', aSbStart, aSbEnd);
      const [aSttsPos] = findBox(data, 'stts', aSbStart, aSbEnd);
      [aStsdPos, aStsdSize] = findBox(data, 'stsd', aSbStart, aSbEnd);
      [aSgpdPos, aSgpdSize] = findBox(data, 'sgpd', aSbStart, aSbEnd);
      [aSbgpPos, aSbgpSize] = findBox(data, 'sbgp', aSbStart, aSbEnd);

      audioChunks = aStcoPos !== null ? parseStco(data, aStcoPos) : parseCo64(data, aCo64Pos);
      audioSizes = parseStsz(data, aStszPos);
      audioStts = parseStts(data, aSttsPos);

      [aMdhdPos, aMdhdSize] = findBoxPath(data, ['mdia', 'mdhd'], atStart, atEnd);
      aTimescale = readU32(data, aMdhdPos + 20);

      const [aElstPos] = findBoxPath(data, ['edts', 'elst'], atStart, atEnd);
      let aElstSegDur = 0, aElstMediaTime = 0;
      if (aElstPos !== null) {
        aElstSegDur = readU32(data, aElstPos + 16);
        aElstMediaTime = readU32(data, aElstPos + 20);
      }

      if (aElstPos !== null && aElstSegDur > 0) {
        newAudioDurMs = aElstSegDur;
      } else {
        const aMdhdDur = readU32(data, aMdhdPos + 24);
        newAudioDurMs = aTimescale > 0 ? Math.floor((aMdhdDur * mvTimescale) / aTimescale) : 0;
      }
      newAudioDur = aTimescale > 0 ? Math.floor((newAudioDurMs * aTimescale) / mvTimescale) : 0;

      const aDelta = audioStts.length ? audioStts[0][1] : 1024;
      const aCount = audioSizes.length;
      const targetStts = newAudioDur + aElstMediaTime;
      const mainN = aCount - 1;
      const lastDelta = targetStts - mainN * aDelta;

      if (mainN > 0 && lastDelta > 0 && lastDelta <= aDelta) {
        newAStts = [[mainN, aDelta], [1, lastDelta]];
      } else {
        newAStts = audioStts;
        newAudioDur = readU32(data, aMdhdPos + 24);
        newAudioDurMs = aTimescale > 0 ? Math.floor((newAudioDur * mvTimescale) / aTimescale) : 0;
      }

      let totalABytes = 0;
      for (let s of audioSizes) totalABytes += s;
      let oldATicks = 0;
      for (let [c, d] of audioStts) oldATicks += c * d;
      let newATicks = 0;
      for (let [c, d] of newAStts) newATicks += c * d;

      oldABr = oldATicks > 0 ? Math.floor((totalABytes * 8 * aTimescale) / oldATicks) : 128000;
      newABr = newATicks > 0 ? Math.floor((totalABytes * 8 * aTimescale) / newATicks) : 128000;
    }
    onProgress(60, 'Rebuilding media data (mdat)...');
    const mdatBefore = data.subarray(mdatDataStart, firstOff);
    const mdatAfter = data.subarray(firstOff + firstSize, mdatPos + mdatSize);
    const newMdatContent = padCount > 0
      ? concatBytes(mdatBefore, newFirst, mdatAfter, PADDING_NAL)
      : concatBytes(mdatBefore, newFirst, mdatAfter);
    const newMdatHeader = new Uint8Array(8);
    writeU32(newMdatHeader, 0, 8 + newMdatContent.length);
    newMdatHeader.set(strToBytes('mdat'), 4);
    const newMdat = concatBytes(newMdatHeader, newMdatContent);

    onProgress(75, 'Constructing metadata header (moov)...');
    const ftyp = buildFtyp();
    const free = buildFree();

    const [mvhdPos, mvhdSize] = findBox(data, 'mvhd', moovPos + 8, moovPos + moovSize);
    const mvhdContent = data.subarray(mvhdPos + 8, mvhdPos + mvhdSize);
    const [vTkhdPos] = findBox(data, 'tkhd', vtStart, vtEnd);
    const vTkhdDurMs = readU32(data, vTkhdPos + 28);
    const newMvhdDur = hasAudio ? Math.max(vTkhdDurMs, newAudioDurMs) : vTkhdDurMs;
    const mvhd = buildMvhd(mvhdContent, newMvhdDur);

    const vTkhdSize = readU32(data, vtStart);
    const videoTkhd = data.subarray(vtStart, vtStart + vTkhdSize);
    const [edtsPos, edtsSize] = findBox(data, 'edts', vtStart, vtEnd);
    let videoEdts = edtsPos ? data.subarray(edtsPos, edtsPos + edtsSize) : new Uint8Array(0);
    if (videoEdts.length === 0 && vTkhdDurMs > 0) {
      const elstContent = new Uint8Array(20);
      writeU32(elstContent, 0, 0);
      writeU32(elstContent, 4, 1);
      writeU32(elstContent, 8, vTkhdDurMs);
      writeU32(elstContent, 12, 0);
      writeU16(elstContent, 16, 1);
      writeU16(elstContent, 18, 0);
      videoEdts = buildBox('edts', buildBox('elst', elstContent));
    }
    const videoMdhd = data.subarray(vMdhdPos, vMdhdPos + vMdhdSize);
    const [vHdlrPos, vHdlrSize] = findBoxPath(data, ['mdia', 'hdlr'], vtStart, vtEnd);
    const videoHdlr = data.subarray(vHdlrPos, vHdlrPos + vHdlrSize);
    const [vmhdPos, vmhdSize] = findBoxPath(data, ['mdia', 'minf', 'vmhd'], vtStart, vtEnd);
    const videoVmhd = data.subarray(vmhdPos, vmhdPos + vmhdSize);
    const [dinfPos, dinfSize] = findBoxPath(data, ['mdia', 'minf', 'dinf'], vtStart, vtEnd);
    const videoDinf = data.subarray(dinfPos, dinfPos + dinfSize);

    const stsdContent = data.subarray(stsdPos + 8, stsdPos + stsdSize);
    const avc1Fixed = stsdContent.subarray(16, 94);

    const avccNeedle = strToBytes('avcC');
    const avccRel = findSubarray(stsdContent, avccNeedle);
    const avccStart = avccRel - 4;
    const avccOrigSize = readU32(stsdContent, avccStart);
    const avccEnd = avccStart + avccOrigSize;
    const avccNew = buildAvcc(stsdContent.subarray(avccStart + 8, avccStart + avccOrigSize));

    let colrBox = new Uint8Array(0);
    const colrRel = findSubarray(stsdContent, strToBytes('colr'), avccEnd);
    if (colrRel >= 0) {
      const cs = colrRel - 4;
      colrBox = stsdContent.subarray(cs, cs + readU32(stsdContent, cs));
    }

    let paspBox = new Uint8Array(0);
    const paspRel = findSubarray(stsdContent, strToBytes('pasp'), avccEnd);
    if (paspRel >= 0) {
      const ps = paspRel - 4;
      paspBox = stsdContent.subarray(ps, ps + readU32(stsdContent, ps));
    }

    let totalVBytes = 0;
    for (let s of videoSizes) totalVBytes += s;
    totalVBytes = totalVBytes - firstSize + newFirstSize;
    const newVAvgBr = vDurationSec > 0 ? Math.floor((totalVBytes * 8) / vDurationSec) : 10000000;

    const btrtRel = findSubarray(stsdContent, strToBytes('btrt'), avccEnd);
    const maxBr = btrtRel >= 0 ? readU32(stsdContent, btrtRel + 8) : newVAvgBr;
    const btrtNew = buildBtrt(0, maxBr, newVAvgBr);

    const videoStsdNew = buildStsdVideo(avc1Fixed, avccNew, colrBox, paspBox, btrtNew);

    let videoSttsNew, videoStscNew, videoStszNew;
    if (padCount > 0) {
      // Ghost frames inherit native timeDelta so the container declared framerate
      // remains exactly 60.0 fps (or 30.0 fps) — avoiding TikTok transcoder 540p downscale rejection.
      // Playback duration remains clipped to exact original length via mvhd/tkhd/edts.
      videoSttsNew = buildStts([...videoStts, [padCount, timeDelta]]);
      videoStscNew = buildStsc([...videoStsc, [videoChunks.length + 1, 1, 1]]);
      const newVideoSizes = [newFirstSize, ...videoSizes.slice(1)];
      for (let i = 0; i < padCount; i++) {
        newVideoSizes.push(PADDING_SIZE);
      }
      videoStszNew = buildStsz(newVideoSizes);
    } else {
      videoSttsNew = buildStts(videoStts);
      videoStscNew = buildStsc(videoStsc);
      videoStszNew = buildStsz([newFirstSize, ...videoSizes.slice(1)]);
    }

    const videoStss = stssPos ? data.subarray(stssPos, stssPos + stssSize) : new Uint8Array(0);
    const videoSdtp = sdtpPos ? data.subarray(sdtpPos, sdtpPos + sdtpSize) : new Uint8Array(0);
    const videoCtts = cttsPos ? data.subarray(cttsPos, cttsPos + cttsSize) : new Uint8Array(0);

    const totalVChunks = videoChunks.length + padCount;
    const stcoPhSize = 16 + totalVChunks * 4;
    const vStcoPh = new Uint8Array(stcoPhSize);
    writeU32(vStcoPh, 0, stcoPhSize);
    vStcoPh.set(strToBytes('stco'), 4);

    const vStblParts = [videoStsdNew, videoSttsNew];
    if (videoStss.length) vStblParts.push(videoStss);
    if (videoSdtp.length) vStblParts.push(videoSdtp);
    if (videoCtts.length) vStblParts.push(videoCtts);
    vStblParts.push(videoStscNew, videoStszNew, vStcoPh);

    let videoStbl = buildBox('stbl', concatBytes(...vStblParts));
    let videoMinf = buildBox('minf', concatBytes(videoVmhd, videoDinf, videoStbl));
    let videoMdia = buildBox('mdia', concatBytes(videoMdhd, videoHdlr, videoMinf));
    let videoTrak = buildBox('trak', concatBytes(videoTkhd, videoEdts, videoMdia));

    let audioTrak = new Uint8Array(0);
    if (hasAudio) {
      const [aTkhdPos, aTkhdSize] = findBox(data, 'tkhd', atStart, atEnd);
      const audioTkhd = buildTkhd(data.subarray(aTkhdPos, aTkhdPos + aTkhdSize), newAudioDurMs);

      const [aEdtsPos, aEdtsSize] = findBox(data, 'edts', atStart, atEnd);
      const audioEdts = aEdtsPos ? data.subarray(aEdtsPos, aEdtsPos + aEdtsSize) : new Uint8Array(0);

      const audioMdhd = buildMdhd(data.subarray(aMdhdPos + 8, aMdhdPos + aMdhdSize), newAudioDur, LANG_UND);
      const audioHdlr = buildHdlr(strToBytes('soun'), 'SoundHandler');

      const [smhdPos, smhdSize] = findBoxPath(data, ['mdia', 'minf', 'smhd'], atStart, atEnd);
      const audioSmhd = data.subarray(smhdPos, smhdPos + smhdSize);
      const [aDinfPos, aDinfSize] = findBoxPath(data, ['mdia', 'minf', 'dinf'], atStart, atEnd);
      const audioDinf = data.subarray(aDinfPos, aDinfPos + aDinfSize);

      const audioStsd = new Uint8Array(data.subarray(aStsdPos, aStsdPos + aStsdSize));
      if (oldABr > 0 && oldABr !== newABr) {
        const oldBrBytes = new Uint8Array(4);
        const newBrBytes = new Uint8Array(4);
        writeU32(oldBrBytes, 0, oldABr);
        writeU32(newBrBytes, 0, newABr);
        let p = 0;
        while (true) {
          const idx = findSubarray(audioStsd, oldBrBytes, p);
          if (idx === -1) break;
          audioStsd.set(newBrBytes, idx);
          p = idx + 4;
        }
      }

      const audioSttsNew = buildStts(newAStts);
      const audioStsc = data.subarray(aStscPos, aStscPos + aStscSize);
      const audioStsz = data.subarray(aStszPos, aStszPos + aStszSize);

      const aStcoPhSize = 16 + audioChunks.length * 4;
      const aStcoPh = new Uint8Array(aStcoPhSize);
      writeU32(aStcoPh, 0, aStcoPhSize);
      aStcoPh.set(strToBytes('stco'), 4);

      const aSgpd = aSgpdPos ? data.subarray(aSgpdPos, aSgpdPos + aSgpdSize) : new Uint8Array(0);
      const aSbgp = aSbgpPos ? data.subarray(aSbgpPos, aSbgpPos + aSbgpSize) : new Uint8Array(0);

      const audioStbl = buildBox('stbl', concatBytes(audioStsd, audioSttsNew, audioStsc, audioStsz, aStcoPh, aSgpd, aSbgp));
      const audioMinf = buildBox('minf', concatBytes(audioSmhd, audioDinf, audioStbl));
      const audioMdia = buildBox('mdia', concatBytes(audioMdhd, audioHdlr, audioMinf));
      audioTrak = buildBox('trak', concatBytes(audioTkhd, audioEdts, audioMdia));
    }

    const udta = (comment && comment.trim().length > 0) ? buildUdtaComment(comment.trim()) : new Uint8Array(0);
    let moov = buildBox('moov', concatBytes(mvhd, videoTrak, audioTrak, udta));
    const moovSizeFinal = moov.length;

    onProgress(88, 'Calculating fast-start sample chunk offsets...');
    const newMdatStart = ftyp.length + free.length + moovSizeFinal + 8;
    const firstVideoRel = firstOff - mdatDataStart;
    const padAbs = newMdatStart + newMdatContent.length - PADDING_SIZE;

    const newVOffsets = [];
    for (let off of videoChunks) {
      const rel = off - mdatDataStart;
      newVOffsets.push(rel <= firstVideoRel ? newMdatStart + rel : newMdatStart + rel - seiRemoved);
    }
    if (padCount > 0) {
      for (let i = 0; i < padCount; i++) {
        newVOffsets.push(padAbs);
      }
    }

    const newAOffsets = [];
    if (hasAudio) {
      for (let off of audioChunks) {
        const rel = off - mdatDataStart;
        newAOffsets.push(rel < firstVideoRel ? newMdatStart + rel : newMdatStart + rel - seiRemoved);
      }
    }

    vStblParts[vStblParts.length - 1] = buildStco(newVOffsets);
    videoStbl = buildBox('stbl', concatBytes(...vStblParts));
    videoMinf = buildBox('minf', concatBytes(videoVmhd, videoDinf, videoStbl));
    videoMdia = buildBox('mdia', concatBytes(videoMdhd, videoHdlr, videoMinf));
    videoTrak = buildBox('trak', concatBytes(videoTkhd, videoEdts, videoMdia));

    if (hasAudio) {
      const [aTkhdPos, aTkhdSize] = findBox(data, 'tkhd', atStart, atEnd);
      const audioTkhd = buildTkhd(data.subarray(aTkhdPos, aTkhdPos + aTkhdSize), newAudioDurMs);
      const [aEdtsPos, aEdtsSize] = findBox(data, 'edts', atStart, atEnd);
      const audioEdts = aEdtsPos ? data.subarray(aEdtsPos, aEdtsPos + aEdtsSize) : new Uint8Array(0);
      const audioMdhd = buildMdhd(data.subarray(aMdhdPos + 8, aMdhdPos + aMdhdSize), newAudioDur, LANG_UND);
      const audioHdlr = buildHdlr(strToBytes('soun'), 'SoundHandler');
      const [smhdPos, smhdSize] = findBoxPath(data, ['mdia', 'minf', 'smhd'], atStart, atEnd);
      const audioSmhd = data.subarray(smhdPos, smhdPos + smhdSize);
      const [aDinfPos, aDinfSize] = findBoxPath(data, ['mdia', 'minf', 'dinf'], atStart, atEnd);
      const audioDinf = data.subarray(aDinfPos, aDinfPos + aDinfSize);

      const audioStsd = new Uint8Array(data.subarray(aStsdPos, aStsdPos + aStsdSize));
      if (oldABr > 0 && oldABr !== newABr) {
        const oldBrBytes = new Uint8Array(4);
        const newBrBytes = new Uint8Array(4);
        writeU32(oldBrBytes, 0, oldABr);
        writeU32(newBrBytes, 0, newABr);
        let p = 0;
        while (true) {
          const idx = findSubarray(audioStsd, oldBrBytes, p);
          if (idx === -1) break;
          audioStsd.set(newBrBytes, idx);
          p = idx + 4;
        }
      }

      const audioSttsNew = buildStts(newAStts);
      const audioStsc = data.subarray(aStscPos, aStscPos + aStscSize);
      const audioStsz = data.subarray(aStszPos, aStszPos + aStszSize);
      const aSgpd = aSgpdPos ? data.subarray(aSgpdPos, aSgpdPos + aSgpdSize) : new Uint8Array(0);
      const aSbgp = aSbgpPos ? data.subarray(aSbgpPos, aSbgpPos + aSbgpSize) : new Uint8Array(0);

      const audioStbl = buildBox('stbl', concatBytes(audioStsd, audioSttsNew, audioStsc, audioStsz, buildStco(newAOffsets), aSgpd, aSbgp));
      const audioMinf = buildBox('minf', concatBytes(audioSmhd, audioDinf, audioStbl));
      const audioMdia = buildBox('mdia', concatBytes(audioMdhd, audioHdlr, audioMinf));
      audioTrak = buildBox('trak', concatBytes(audioTkhd, audioEdts, audioMdia));
    }

    moov = buildBox('moov', concatBytes(mvhd, videoTrak, audioTrak, udta));
    if (moov.length !== moovSizeFinal) {
      throw new Error(`Moov length verification failed: expected ${moovSizeFinal}, got ${moov.length}`);
    }

    onProgress(98, 'Packaging final MP4 container...');
    const output = concatBytes(ftyp, free, moov, newMdat);
    onProgress(100, 'Optimization complete!');

    return {
      output,
      stats: {
        input_size: fileSize,
        output_size: output.length,
        size_delta: output.length - fileSize,
        original_frames: origFrames,
        declared_frames: totalFrames,
        ghost_frames: padCount,
        multiplier: multiplier,
        duration_sec: Number(vDurationSec.toFixed(2))
      }
    };
  }

  /**
   * 7-u8 Smoothness Trick (Community 120 FPS / 60 FPS timescale patch method)
   * Modifies mvhd and mdhd timescale & duration by scale = targetFps / sourceFps.
   * Keeps the playback duration in seconds 100% EXACT (13s stays 13s, 0% stretch, 0 frozen frames).
   * TikTok's transcoder keeps all 60 frames intact without downscaling to 540p or auto-privating!
   */
  function patchFps(data, options = {}) {
    const onProgress = options.onProgress || (() => {});
    onProgress(10, 'Scanning MP4 structure...');

    const [moovPos, moovSize] = findBox(data, 'moov');
    if (moovPos === null) {
      throw new Error("Invalid MP4: No 'moov' box found.");
    }
    const [mvhdPos, mvhdSize] = findBox(data, 'mvhd', moovPos + 8, moovPos + moovSize);
    if (mvhdPos === null) {
      throw new Error("Invalid MP4: No 'mvhd' box found inside 'moov'.");
    }

    // Determine mvhd version, timescale, and duration offsets
    const mvhdBody = mvhdPos + 8;
    const mvhdVer = data[mvhdBody];
    let mvTsOff, mvDurOff, mvDurLen;
    if (mvhdVer === 0) {
      mvTsOff = mvhdBody + 12;
      mvDurOff = mvhdBody + 16;
      mvDurLen = 4;
    } else if (mvhdVer === 1) {
      mvTsOff = mvhdBody + 20;
      mvDurOff = mvhdBody + 24;
      mvDurLen = 8;
    } else {
      throw new Error(`Unsupported mvhd version: ${mvhdVer}`);
    }

    const oldMvTs = readU32(data, mvTsOff);
    const oldMvDur = mvDurLen === 4 ? readU32(data, mvDurOff) : Number((BigInt(readU32(data, mvDurOff)) << 32n) | BigInt(readU32(data, mvDurOff + 4)));

    // Locate video track to detect source FPS and duration
    onProgress(25, 'Detecting frame rate & track timings...');
    const [vtPos, vtSize] = findTrackByHandler(data, moovPos, moovSize, strToBytes('vide'));
    if (vtPos === null) {
      throw new Error("No video track found with handler 'vide'.");
    }

    const [vMdhdPos, vMdhdSize] = findBoxPath(data, ['mdia', 'mdhd'], vtPos + 8, vtPos + vtSize);
    if (vMdhdPos === null) {
      throw new Error("Missing video 'mdhd' box.");
    }
    const vMdhdBody = vMdhdPos + 8;
    const vMdhdVer = data[vMdhdBody];
    let vTsOff, vDurOff, vDurLen;
    if (vMdhdVer === 0) {
      vTsOff = vMdhdBody + 12;
      vDurOff = vMdhdBody + 16;
      vDurLen = 4;
    } else if (vMdhdVer === 1) {
      vTsOff = vMdhdBody + 20;
      vDurOff = vMdhdBody + 24;
      vDurLen = 8;
    } else {
      throw new Error(`Unsupported video mdhd version: ${vMdhdVer}`);
    }

    const oldVTs = readU32(data, vTsOff);
    const oldVDur = vDurLen === 4 ? readU32(data, vDurOff) : Number((BigInt(readU32(data, vDurOff)) << 32n) | BigInt(readU32(data, vDurOff + 4)));
    const vDurationSec = oldVTs > 0 ? (oldVDur / oldVTs) : (oldMvTs > 0 ? oldMvDur / oldMvTs : 0);

    // Calculate source FPS from STTS if not supplied
    let detectedFps = null;
    let sampleCount = 0;
    const [stblPos, stblSize] = findBoxPath(data, ['mdia', 'minf', 'stbl'], vtPos + 8, vtPos + vtSize);
    if (stblPos !== null) {
      const [sttsPos] = findBox(data, 'stts', stblPos + 8, stblPos + stblSize);
      if (sttsPos !== null) {
        const entryCount = readU32(data, sttsPos + 12);
        let totalTicks = 0;
        for (let i = 0; i < entryCount; i++) {
          const c = readU32(data, sttsPos + 16 + i * 8);
          const d = readU32(data, sttsPos + 20 + i * 8);
          sampleCount += c;
          totalTicks += c * d;
        }
        if (totalTicks > 0 && oldVTs > 0) {
          detectedFps = (sampleCount * oldVTs) / totalTicks;
        }
      }
    }

    let sourceFps = options.fpsIn || detectedFps || 60.0;
    let targetFps = options.fpsOut || 30.0;
    const mode = options.mode || 'method';
    const includeAudio = options.includeAudio !== false;

    const scale = targetFps / sourceFps;
    onProgress(50, `Applying timescale patch (${sourceFps.toFixed(2)} -> ${targetFps} FPS, scale ${scale.toFixed(4)})...`);

    // Create patched copy
    const output = new Uint8Array(data.length);
    output.set(data);

    // Patch MVHD
    const newMvTs = Math.max(1, Math.round(oldMvTs * scale));
    writeU32(output, mvTsOff, newMvTs);
    if (mode === 'method') {
      const newMvDur = Math.max(0, Math.round(oldMvDur * scale));
      if (mvDurLen === 4) {
        writeU32(output, mvDurOff, newMvDur);
      } else {
        const bi = BigInt(newMvDur);
        writeU32(output, mvDurOff, Number(bi >> 32n));
        writeU32(output, mvDurOff + 4, Number(bi & 0xffffffffn));
      }
    }

    // Find all trak boxes and patch mdhd
    let pos = moovPos + 8;
    const moovEnd = moovPos + moovSize;
    let patchedTracks = 0;

    while (pos + 8 <= moovEnd) {
      const trakSize = readU32(data, pos);
      const btype = bytesToStr(data, pos + 4, 4);
      if (trakSize < 8) break;

      if (btype === 'trak') {
        const [hdlrPos] = findBoxPath(data, ['mdia', 'hdlr'], pos + 8, pos + trakSize);
        let handler = '????';
        if (hdlrPos !== null) {
          handler = bytesToStr(data, hdlrPos + 16, 4);
        }

        const isVideo = handler === 'vide';
        const isAudio = handler === 'soun';

        if (isVideo || (isAudio && includeAudio)) {
          const [mdhdPos] = findBoxPath(data, ['mdia', 'mdhd'], pos + 8, pos + trakSize);
          if (mdhdPos !== null) {
            const mBody = mdhdPos + 8;
            const mVer = data[mBody];
            let tsO, durO, durL;
            if (mVer === 0) {
              tsO = mBody + 12;
              durO = mBody + 16;
              durL = 4;
            } else if (mVer === 1) {
              tsO = mBody + 20;
              durO = mBody + 24;
              durL = 8;
            }
            if (tsO) {
              const oldTs = readU32(data, tsO);
              const oldDur = durL === 4 ? readU32(data, durO) : Number((BigInt(readU32(data, durO)) << 32n) | BigInt(readU32(data, durO + 4)));
              const newTs = Math.max(1, Math.round(oldTs * scale));
              writeU32(output, tsO, newTs);

              if (mode === 'method') {
                const newDur = Math.max(0, Math.round(oldDur * scale));
                if (durL === 4) {
                  writeU32(output, durO, newDur);
                } else {
                  const bi = BigInt(newDur);
                  writeU32(output, durO, Number(bi >> 32n));
                  writeU32(output, durO + 4, Number(bi & 0xffffffffn));
                }
              }
              patchedTracks++;
            }
          }
        }
      }
      pos += trakSize;
    }

    onProgress(100, '7-u8 Smoothness patch complete!');

    return {
      output,
      stats: {
        input_size: data.length,
        output_size: output.length,
        size_delta: 0,
        original_frames: sampleCount,
        declared_frames: sampleCount,
        ghost_frames: 0,
        multiplier: 1,
        source_fps: Number(sourceFps.toFixed(2)),
        target_fps: Number(targetFps.toFixed(2)),
        scale: Number(scale.toFixed(4)),
        duration_sec: Number(vDurationSec.toFixed(2)),
        tracks_patched: patchedTracks,
        mode: mode
      }
    };
  }

  // Export to window / worker or CommonJS
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { transformMp4, patchFps };
  } else if (typeof global !== 'undefined') {
    global.TikTokQuality = { transformMp4, patchFps };
  }
})(typeof self !== 'undefined' ? self : this);
